# SonarQube and Trivy Setup

This guide describes the GitHub Actions workflow at `.github/workflows/ci-cd.yml`, which analyzes Java source with SonarQube and scans the built container image with Trivy before publishing it to Docker Hub.

**Temporary status:** SonarQube/Trivy CSV generation and S3 report uploads are commented out in the workflow for now. The SonarQube quality gate and Trivy vulnerability gate remain enabled. The report-export and S3 setup below documents the planned configuration for when those workflow steps are re-enabled.

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

Point a DNS name you own, such as `sonar.your-company.com`, to the EC2 public address (or a load balancer in front of it). `sonar.example.com` used in examples below is a placeholder under a reserved example domain; it cannot be used to obtain a certificate. Replace it everywhere with your real DNS name. The existing workflow uses GitHub-hosted runners, so `SONAR_HOST_URL` must be an HTTPS endpoint reachable from those runners. If policy requires SonarQube to stay private, install a self-hosted GitHub Actions runner inside the VPC and change the workflow's build job from `ubuntu-latest` to that runner's labels; do not open port 9000 to the internet.

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

Install Nginx and Certbot, then create a reverse-proxy site. Replace every `sonar.example.com` below with the real DNS name you own and have pointed at the instance. Before requesting the certificate, confirm that the DNS record resolves to this server and inbound TCP ports 80 and 443 are reachable from the internet; Certbot's default HTTP validation needs port 80:

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

The command above still contains the example placeholder: substitute your actual hostname (for example, `sudo certbot --nginx -d sonar.your-company.com`) before running it. Verify `https://<your-real-hostname>` loads. Keep inbound TCP 9000 closed in the EC2 security group; GitHub Actions should use the HTTPS endpoint.

Open the site, sign in with the initial `admin` credentials, and immediately change the password. Back up the PostgreSQL and SonarQube data volumes, and keep Ubuntu, Docker, PostgreSQL, and SonarQube patched.

## 5. Create the SonarQube Project and Token

1. In SonarQube, create a production project for this GitHub repository and a separate project for the `Dev` branch.
2. Copy the production project key as `SONAR_PROJECT_KEY` and the Dev project key as `SONAR_PROJECT_KEY_DEV`.
3. Create a project analysis token and copy it once. Treat it as a password.
4. Confirm the project quality gate and quality profile are enabled. The workflow waits for the quality gate and fails if it does not pass.

## 6. Configure GitHub Actions Variables and Secrets

In the GitHub repository, open **Settings → Secrets and variables → Actions**.

Add these repository variables:

| Variable | Value |
| --- | --- |
| `SONAR_HOST_URL` | SonarQube server URL, including scheme, such as `https://sonar.example.com` |
| `SONAR_PROJECT_KEY` | The exact production/main project key created in SonarQube |
| `SONAR_PROJECT_KEY_DEV` | A separate SonarQube project key for the `Dev` branch |
| `AWS_ROLE_ARN` | ARN of the AWS IAM role GitHub Actions assumes through OIDC |
| `AWS_REGION` | AWS region containing the report bucket |
| `S3_BUCKET` | Name of the private S3 bucket for scan reports |

Add these repository secrets:

| Secret | Value |
| --- | --- |
| `SONAR_TOKEN` | SonarQube project analysis token |
| `SONAR_REPORT_TOKEN` | SonarQube token whose user has Browse permission on the project |
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token with permission to push the image |

Never commit tokens or passwords. The report token is separate because exporting issues through SonarQube's Web API requires project Browse permission; grant it only the access needed to read this project.

### Configure GitHub Actions access to S3

Use GitHub's OIDC federation rather than long-lived AWS access keys. Configure the AWS account's GitHub OIDC provider (`token.actions.githubusercontent.com`) and an IAM role trusted only for this repository's `main` branch. Its trust policy should restrict the audience to `sts.amazonaws.com` and the subject to:

```text
repo:mangeshchauhan/Student-Management-Application:ref:refs/heads/main
```

Grant that role only `s3:PutObject` on the report prefix:

```text
arn:aws:s3:::YOUR_PRIVATE_BUCKET/security-reports/mangeshchauhan/Student-Management-Application/*
```

Replace `YOUR_PRIVATE_BUCKET` with the bucket configured in `S3_BUCKET`. Keep S3 Block Public Access enabled. The workflow uploads reports with SSE-S3 (`AES256`); configure bucket lifecycle rules if reports should expire automatically.

When re-enabled, the report flow will set the SonarScanner working directory to `.scannerwork`, verify its `report-task.txt`, export the project's current SonarQube issues through `/api/issues/search`, and convert Trivy JSON output to CSV. Both reports will upload under a unique path containing the GitHub run ID, attempt, and commit SHA. The SonarQube CSV represents the project's current issues, not only issues introduced by that commit.

## 7. Run the Workflow

The workflow runs when code is pushed to `main`:

```powershell
git add .
git commit -m "Add SonarQube and Trivy scans"
git push origin main
```

Open the repository’s **Actions** tab and check the `build` job first. If it fails, inspect the Maven/SonarQube output and confirm the host URL, token, project key, and runner network access. The `docker` job runs only after the build and quality gate pass.

The image tags depend on the source branch:

```text
main: <DOCKERHUB_USERNAME>/student-management:<commit-sha>
main: <DOCKERHUB_USERNAME>/student-management:build-<run-number>-<commit-sha>
main: <DOCKERHUB_USERNAME>/student-management:latest
Dev:  <DOCKERHUB_USERNAME>/student-management:dev-build-<run-number>-<commit-sha>
Dev:  <DOCKERHUB_USERNAME>/student-management:dev-latest
```

The production and Dev Image Updaters filter different tag prefixes. Deployments use the ordered immutable build tags; mutable `latest` and `dev-latest` tags alone do not trigger Argo CD updates. CSV report exports and S3 uploads are currently disabled in the workflow, as noted at the top of this guide.

## 8. Trivy Runs in GitHub Actions

The workflow uses `aquasecurity/trivy-action` on the GitHub runner. It scans the locally built image before Docker Hub login and push, writes `trivy-results.json`, converts vulnerabilities to CSV, and uploads the CSV to S3. Trivy does not need to be installed on the SonarQube EC2 instance. The scan checks operating-system and application-library vulnerabilities, blocks HIGH/CRITICAL findings when a fix is available, and ignores unfixed findings. A failed scan still uploads its report but prevents the image tags from being pushed.

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
