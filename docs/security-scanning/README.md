# SonarQube and Trivy Setup

This guide configures the GitHub Actions workflow at `.github/workflows/ci-cd.yml` to analyze Java source with SonarQube and scan the built container image with Trivy before publishing it to Docker Hub.

## Workflow Behavior

1. The Maven job runs `clean verify`, uploads analysis to SonarQube, and waits for the SonarQube quality gate.
2. Only after that job passes, the image job builds the Docker image and scans its OS and application-library vulnerabilities with Trivy.
3. Findings rated HIGH or CRITICAL that have a fix available fail the job. A failed SonarQube quality gate or Trivy scan prevents the Docker Hub push.
4. On success, the exact scanned image is pushed as both the Git commit SHA and `latest`.

Trivy is configured with `ignore-unfixed: true`; HIGH/CRITICAL vulnerabilities without a known fix are reported as excluded and do not block publishing. Review those findings separately and update the base image/dependencies as fixes become available.

## 1. Provision the SonarQube EC2 Instance

Create an Ubuntu 22.04 or 24.04 EC2 instance in a subnet that can reach the internet for image/package updates. For a small team, start with at least 4 vCPU and 8 GiB RAM; increase capacity for larger projects. Attach an encrypted EBS volume for persistent data and backups.

Configure the instance security group:

- Allow SSH (TCP 22) only from your administrator IP or a bastion.
- Allow HTTP (TCP 80) and HTTPS (TCP 443) only as needed for DNS validation and the HTTPS endpoint.
- Do not expose SonarQube's TCP 9000 or PostgreSQL's TCP 5432 publicly.

Point a DNS name such as `sonar.example.com` to the EC2 public address (or a load balancer in front of it). The existing workflow uses GitHub-hosted runners, so `SONAR_HOST_URL` must be an HTTPS endpoint reachable from those runners. If policy requires SonarQube to stay private, install a self-hosted GitHub Actions runner inside the VPC and change the workflow's build job from `ubuntu-latest` to that runner's labels; do not open port 9000 to the internet.

## 2. Install Docker Engine and Prepare Linux

Connect over SSH, add Docker's official Ubuntu package repository, then install Docker Engine and the Compose plugin:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

Set the kernel limits required by the SonarQube Elasticsearch process:

```bash
sudo tee /etc/sysctl.d/99-sonarqube.conf >/dev/null <<'EOF'
vm.max_map_count=524288
fs.file-max=131072
EOF
sudo sysctl --system
```

## 3. Create a Persistent SonarQube and PostgreSQL Stack

Create `/opt/sonarqube`, then create a root-readable environment file for the PostgreSQL password. Generate a unique password locally and do not commit this file:

```bash
sudo install -d -m 0750 /opt/sonarqube
SONAR_DB_PASSWORD=$(openssl rand -hex 32)
printf 'SONAR_DB_PASSWORD=%s\n' "$SONAR_DB_PASSWORD" | sudo tee /opt/sonarqube/.env >/dev/null
sudo chmod 600 /opt/sonarqube/.env
unset SONAR_DB_PASSWORD
```

Create `/opt/sonarqube/compose.yaml` with this content:

```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: sonar
      POSTGRES_PASSWORD: ${SONAR_DB_PASSWORD:?Set SONAR_DB_PASSWORD in .env}
      POSTGRES_DB: sonar
    volumes:
      - postgresql_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sonar -d sonar"]
      interval: 10s
      timeout: 5s
      retries: 10

  sonarqube:
    image: sonarqube:community
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      SONAR_JDBC_URL: jdbc:postgresql://db:5432/sonar
      SONAR_JDBC_USERNAME: sonar
      SONAR_JDBC_PASSWORD: ${SONAR_DB_PASSWORD:?Set SONAR_DB_PASSWORD in .env}
    ports:
      - "127.0.0.1:9000:9000"
    volumes:
      - sonarqube_data:/opt/sonarqube/data
      - sonarqube_extensions:/opt/sonarqube/extensions
      - sonarqube_logs:/opt/sonarqube/logs
    ulimits:
      nofile:
        soft: 131072
        hard: 131072
      nproc:
        soft: 8192
        hard: 8192

volumes:
  postgresql_data:
  sonarqube_data:
  sonarqube_extensions:
  sonarqube_logs:
```

Start the services and wait for SonarQube to initialize:

```bash
cd /opt/sonarqube
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs -f sonarqube
```

The first startup can take several minutes. The SonarQube port is bound to localhost only; put Nginx and TLS in front of it rather than changing this to `0.0.0.0:9000`.

## 4. Configure Nginx and HTTPS

Install Nginx and Certbot, then create a reverse-proxy site. Replace `sonar.example.com` with the DNS name pointed at the instance:

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo tee /etc/nginx/sites-available/sonarqube >/dev/null <<'EOF'
server {
  listen 80;
  server_name sonar.example.com;

  location / {
    proxy_pass http://127.0.0.1:9000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    proxy_buffering off;
  }
}
EOF
sudo ln -s /etc/nginx/sites-available/sonarqube /etc/nginx/sites-enabled/sonarqube
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d sonar.example.com
```

Verify `https://sonar.example.com` loads. Keep inbound TCP 9000 closed in the EC2 security group; GitHub Actions should use the HTTPS endpoint.

Open the site, sign in with the initial `admin` credentials, and immediately change the password. Back up the PostgreSQL and SonarQube data volumes, and keep Ubuntu, Docker, PostgreSQL, and SonarQube patched.

## 5. Create the SonarQube Project and Token

1. In SonarQube, create a project for this GitHub repository.
2. Copy its exact project key; the workflow sends this as `SONAR_PROJECT_KEY`.
3. Create a project analysis token and copy it once. Treat it as a password.
4. Confirm the project quality gate and quality profile are enabled. The workflow waits for the quality gate and fails if it does not pass.

## 6. Configure GitHub Actions Variables and Secrets

In the GitHub repository, open **Settings → Secrets and variables → Actions**.

Add these repository variables:

| Variable | Value |
| --- | --- |
| `SONAR_HOST_URL` | SonarQube server URL, including scheme, such as `https://sonar.example.com` |
| `SONAR_PROJECT_KEY` | The exact project key created in SonarQube |

Add these repository secrets:

| Secret | Value |
| --- | --- |
| `SONAR_TOKEN` | SonarQube project analysis token |
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token with permission to push the image |

Never commit tokens or passwords. If Docker Hub secrets already exist for the current workflow, keep them and add only the SonarQube values.

## 7. Run the Workflow

The workflow runs when code is pushed to `main`:

```powershell
git add .
git commit -m "Add SonarQube and Trivy scans"
git push origin main
```

Open the repository’s **Actions** tab and check the `build` job first. If it fails, inspect the Maven/SonarQube output and confirm the host URL, token, project key, and runner network access. The `docker` job runs only after the build and quality gate pass.

The image tags are:

```text
<DOCKERHUB_USERNAME>/student-management:<commit-sha>
<DOCKERHUB_USERNAME>/student-management:latest
```

Deployments should prefer the commit-SHA tag for reproducibility. `latest` is mutable and by itself does not cause Kubernetes or Argo CD to restart pods.

## 8. Trivy Runs in GitHub Actions

The current workflow uses `aquasecurity/trivy-action` on the GitHub runner. It scans the locally built image before Docker Hub login and push; Trivy does not need to be installed on the SonarQube EC2 instance. The scan checks operating-system and application-library vulnerabilities, blocks HIGH/CRITICAL findings when a fix is available, and ignores unfixed findings. A failed scan prevents both image tags from being pushed.

### Install Trivy on EC2 for Manual Scans (Optional)

On the Ubuntu EC2 instance, install Trivy from Aqua Security's Debian repository:

```bash
sudo apt-get update
sudo apt-get install -y wget gnupg
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | gpg --dearmor | sudo tee /usr/share/keyrings/trivy.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main" | sudo tee /etc/apt/sources.list.d/trivy.list
sudo apt-get update
sudo apt-get install -y trivy
trivy --version
```

The EC2 host must have Docker installed and be able to access the image registry. Log in to Docker Hub using a token without putting it in shell history, then pull and scan the exact commit tag:

```bash
docker login --username YOUR_DOCKERHUB_USERNAME
docker pull YOUR_DOCKERHUB_USERNAME/student-management:COMMIT_SHA
trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 YOUR_DOCKERHUB_USERNAME/student-management:COMMIT_SHA
```

Docker prompts for the Docker Hub token after `docker login`; do not pass the token directly as a command-line argument. Exit code `1` means Trivy found at least one configured blocking vulnerability. Review the package and fixed-version details, update the base image or dependency, rebuild, and scan again.
