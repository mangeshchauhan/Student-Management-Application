# EC2 Deployment (Jenkins, Spring Boot, and RDS)

This guide covers the legacy virtual-machine deployment path in this repository: Jenkins builds the application JAR and deploys it over SSH/SCP to an Ubuntu EC2 application server. The application connects to Amazon RDS for MySQL.

For the containerized Kubernetes deployment, use the [EKS deployment guide](../eks/README.md). For SonarQube analysis, Trivy image scanning, and exporting scan reports to S3, see the [security scanning guide](../../security-scanning/README.md).

## Deployment flow

```text
Developer push
     |
     v
GitHub repository <-- Jenkins EC2 (checkout, Maven test/build)
                              |
                         SSH / SCP
                              v
                    Application EC2
                    Spring Boot + systemd
                              |
                          MySQL 3306
                              v
                         Amazon RDS
```

The Jenkins pipeline is defined in the repository's `Jenkinsfile`. It runs `mvn -B clean verify`, archives the generated JAR, copies it to the application EC2, restarts `student-management.service`, and checks `http://localhost:8080/login`.

## 1. Prepare the AWS network and database

Create or select a VPC with an application subnet and an RDS subnet group. Prefer private subnets for both EC2 instances; provide outbound access through a NAT gateway or another approved egress path.

Configure security groups narrowly:

- **Jenkins EC2:** allow SSH from your administrator IP/bastion. Allow the Jenkins UI only from trusted addresses or a VPN/reverse proxy. Do not expose Jenkins broadly to the internet.
- **Application EC2:** allow SSH (TCP 22) only from the Jenkins security group and administrators. Allow HTTP/HTTPS (TCP 80/443) only from the intended users or load balancer. Do not expose TCP 8080 publicly when Nginx or a load balancer is used.
- **RDS:** allow MySQL TCP 3306 only from the application EC2 security group. Do not make the database publicly accessible.

Create an RDS MySQL database and an application database/user. Record the endpoint, database name, username, and password securely. Ensure the EC2 subnet and RDS subnet group can route to one another.

## 2. Prepare the application EC2 server

Launch a supported Ubuntu EC2 instance. Connect as the default Ubuntu user, create the application directory, and install Java 17, which matches `pom.xml`:

```bash
sudo apt-get update
sudo apt-get install -y openjdk-17-jre-headless nginx
java -version
sudo install -d -o ubuntu -g ubuntu -m 0750 /home/ubuntu/student-management
```

Create the runtime environment file without committing database credentials. Enter the real RDS details when prompted:

```bash
read -r -p "RDS endpoint: " DB_HOST
read -r -p "Database name: " DB_NAME
read -r -p "Database user: " DB_USER
read -r -s -p "Database password: " DB_PASSWORD
printf '\n'
sudo install -o ubuntu -g ubuntu -m 0600 /dev/null /home/ubuntu/student-management/.env
sudo tee /home/ubuntu/student-management/.env >/dev/null <<EOF
SERVER_PORT=8080
DB_HOST=${DB_HOST}
DB_PORT=3306
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
EOF
unset DB_HOST DB_NAME DB_USER DB_PASSWORD
```

Install the systemd service unit from the repository and enable it. Run these commands from the checked-out repository directory:

```bash
sudo install -m 0644 deploy/student-management.service /etc/systemd/system/student-management.service
sudo systemctl daemon-reload
sudo systemctl enable student-management
```

The checked-in service unit runs the JAR from `/home/ubuntu/student-management/student-management.jar`. Ensure that the application's user can read the JAR and `.env` file.

Configure Nginx as a reverse proxy if serving through HTTP/HTTPS. The example in `deploy/nginx.conf` is HTTP-only; configure TLS with a hostname and certificate before using it for public production traffic. Keep direct inbound access to port 8080 restricted.

## 3. Prepare Jenkins and SSH deployment access

Install Jenkins on a separate supported host and configure a Jenkins agent with Git, Java 17, and Maven available. Create a Jenkins SSH credential with ID:

```text
student-management-app-ssh
```

Install the matching public key for the `ubuntu` deployment user on the application EC2. Verify Jenkins can connect to the application host over TCP 22. Do not store the private key in the repository or pipeline file.

The Jenkinsfile prompts for:

- `APP_SERVER_HOST`: the application EC2 private IP or DNS name reachable by the Jenkins agent.
- `APP_SERVER_USER`: normally `ubuntu`.

The pipeline currently disables SSH host-key checking. For a production Jenkins setup, configure host-key verification with a managed `known_hosts` file rather than relying on disabled checking.

## 4. Build and deploy

Run the Jenkins pipeline with the target host and user parameters. It checks out the configured branch, runs tests and packages the application, archives `target/*.jar`, then copies and installs the executable Spring Boot JAR.

To run the same build locally for troubleshooting:

```bash
mvn -B clean verify
```

After deployment, check the service and endpoint on the application EC2:

```bash
sudo systemctl status student-management --no-pager
sudo journalctl -u student-management -n 100 --no-pager
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:8080/login
```

The login endpoint may redirect; a successful HTTP response or redirect confirms the server is answering. Check the Jenkins stage output if deployment or its health check fails.

## 5. Troubleshoot database connectivity

From the application EC2, verify network reachability to RDS:

```bash
nc -zv YOUR_RDS_ENDPOINT 3306
```

If the application exits during startup, inspect the systemd journal and verify the `.env` file values, the RDS security-group source, database name/user, and the password. Never paste passwords into logs or issue reports.

## Notes about repository helper files

- `deploy/setup-ec2.sh` installs a local MySQL server and contains example database credentials. **Do not run it unchanged for an RDS deployment or use its sample password.**
- `deploy/.env.example` contains placeholders only. Keep the production `.env` file on the EC2 host and restrict its file permissions.
- `deploy/student-management.service` is the systemd unit used by this guide.
- The CI/CD container pipeline in `.github/workflows/ci-cd.yml` is separate from this Jenkins JAR deployment.
