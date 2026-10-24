# 🎓 Student Management System

> A full-stack Student Management System built with **Spring Boot, Thymeleaf, MySQL, Jenkins, and AWS**, with an automated CI/CD pipeline for building, testing, and deploying the application to an AWS EC2 server.

![Java](https://img.shields.io/badge/Java-21-orange?logo=openjdk)
![Spring Boot](https://img.shields.io/badge/Spring%20Boot-3.x-brightgreen?logo=springboot)
![MySQL](https://img.shields.io/badge/MySQL-8-blue?logo=mysql)
![Maven](https://img.shields.io/badge/Maven-Build-C71A36?logo=apachemaven)
![Jenkins](https://img.shields.io/badge/Jenkins-CI%2FCD-D24939?logo=jenkins)
![AWS](https://img.shields.io/badge/AWS-Cloud-FF9900?logo=amazonaws)
![EC2](https://img.shields.io/badge/AWS-EC2-FF9900?logo=amazonec2)
![RDS](https://img.shields.io/badge/AWS-RDS-527FFF?logo=amazonrds)
![Linux](https://img.shields.io/badge/Linux-Ubuntu-E95420?logo=ubuntu)

---

## 📌 Project Overview

The Student Management System provides a web-based interface for managing student records. This repository contains both a legacy EC2/Jenkins deployment path and a containerized EKS deployment path. Choose the deployment guide for your target:

- [EC2 deployment (Jenkins, Spring Boot, and RDS)](docs/deployment/ec2/README.md)
- [EKS deployment (Docker, Argo CD, and Image Updater)](docs/deployment/eks/README.md)

The legacy deployment runs the application on an **AWS EC2 Application Server** and stores student data in **Amazon RDS for MySQL**. Its Jenkins pipeline is hosted on a separate EC2 instance and automatically:

1. Checks out the source code from GitHub
2. Builds and tests the application using Maven
3. Creates the Spring Boot JAR
4. Archives the build artifact
5. Transfers the JAR to the Application EC2 using SSH/SCP
6. Installs the new JAR
7. Restarts the systemd service
8. Verifies that the application service is running

---

# 🏗️ Legacy EC2/Jenkins Architecture

### Architecture Diagram

![Student Management AWS Architecture](docs/architecture/student-management-architecture.png)

### Application Flow

```text
                         Developer
                             |
                             | git push
                             v
                    +----------------+
                    |    GitHub      |
                    | Student Repo   |
                    +-------+--------+
                            |
                          HTTPS
                            |
                            v
                    +----------------+
                    |   Jenkins EC2  |
                    |                |
                    | Jenkins        |
                    | Java 21        |
                    | Maven          |
                    +-------+--------+
                            |
                         SSH / SCP
                            |
                            v
                    +----------------+
                    | Application EC2|
                    |                |
                    | Java 21        |
                    | Spring Boot    |
                    | Port 8080      |
                    | systemd        |
                    +-------+--------+
                            |
                         JDBC :3306
                            |
                            v
                    +----------------+
                    |   Amazon RDS   |
                    |     MySQL      |
                    |   studentdb    |
                    +----------------+
```

The Jenkins-to-Application Server deployment uses SSH/SCP, while GitHub source checkout and application deployment are separate connections.

---

# 🛠️ Technology Stack

### Application

- Java 21
- Spring Boot 3
- Spring MVC
- Spring Security
- Thymeleaf
- Bootstrap
- MySQL
- REST APIs
- Maven

### DevOps & Cloud

- AWS EC2
- Amazon RDS
- AWS VPC
- Jenkins
- Git
- GitHub
- SSH
- SCP
- Linux / Ubuntu
- systemd
- Nginx

---

# 🔄 CI/CD Pipeline

GitHub Actions builds the Spring Boot application and pushes the container image to Docker Hub. Argo CD synchronizes the Kubernetes resources from the `manifests/` directory; it does not build or publish the image.

The GitHub Actions pipeline also runs SonarQube code analysis and a Trivy container-image scan before pushing, then exports scan reports to S3. See the [SonarQube and Trivy setup guide](docs/security-scanning/README.md) for server setup, GitHub variables/secrets, and local scan commands. For ordered deployment steps, use the separate [EC2 guide](docs/deployment/ec2/README.md) or [EKS guide](docs/deployment/eks/README.md).

## EKS Deployment Runbook

Follow these steps in order after the EKS cluster and worker nodes are created. Commands below use PowerShell unless otherwise noted. Replace every `YOUR_...` value with your own configuration; never commit passwords, access tokens, or private keys.

### 1. Connect to the intended EKS cluster

Configure AWS CLI credentials for an IAM identity authorized to access the cluster, then update the local kubeconfig:

```powershell
aws eks update-kubeconfig --region YOUR_AWS_REGION --name YOUR_CLUSTER_NAME
kubectl config current-context
kubectl get nodes -o wide
```

Continue only after `kubectl get nodes` shows the expected Ready nodes in the intended cluster.

### 2. Install and verify the EKS cluster add-ons

Before deploying this application's manifests, install and configure:

- **Amazon EBS CSI driver**, with its required IAM role, for the MySQL persistent volume.
- **AWS Load Balancer Controller**, with its required IAM permissions, for the ALB Ingress.

Use the AWS EKS documentation for [the EBS CSI driver](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html) and [AWS Load Balancer Controller](https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html). Verify the controllers and available storage classes:

```powershell
kubectl get pods -n kube-system
kubectl get storageclass
```

Do not proceed if the EBS CSI controller or AWS Load Balancer Controller is not healthy.

### 3. Prepare and verify MySQL persistent storage

This repository currently uses a **static EBS PersistentVolume**. Before syncing the application, open [mysql-pv.yaml](manifests/mysql-pv.yaml) and verify `volumeHandle` is the ID of the EBS volume you intend to use and the node-affinity availability zone matches that volume's zone. The volume must be in the same AWS region as the cluster and must not be mounted by another database. Do not point a new database at a volume containing data you do not intend to retain.

The PersistentVolume and claim currently use the `gp2` storage class, while [storageclass.yaml](manifests/storageclass.yaml) defines a separate `ebs-gp3-retain` class. The static PV and PVC must use the same storage class name; make sure the EKS cluster has a matching class or update both manifests consistently before deployment. The `ebs-gp3-retain` resource alone does not change the claim's storage class.

After applying the application in step 7, verify the claim binds before expecting MySQL to start:

```powershell
kubectl get pv
kubectl get pvc -n student-management
kubectl describe pvc mysql-data -n student-management
```

### 4. Prepare S3 and the two separate AWS roles

Create a private S3 bucket with Block Public Access enabled. Configure **two separate least-privilege IAM roles**:

1. **EKS backup role:** IRSA trust for `system:serviceaccount:student-management:student-management-backup`, with `s3:PutObject` and `s3:AbortMultipartUpload` scoped to `YOUR_BUCKET/student-management/mysql/*`. Update the bucket, region, and role ARN placeholders in [backup-config.yaml](backups/backup-config.yaml) and [backup-serviceaccount.yaml](backups/backup-serviceaccount.yaml).
2. **GitHub Actions report role:** GitHub OIDC trust restricted to this repository's `main` branch, with `s3:PutObject` scoped to `YOUR_BUCKET/security-reports/mangeshchauhan/Student-Management-Application/*`. Set this role ARN, bucket, and region in the GitHub repository variables in step 8.

Do not reuse the EKS role as the GitHub Actions role. The reports and database backups use separate S3 prefixes and identities. The detailed GitHub OIDC trust and policy requirements are in the [security-scanning guide](docs/security-scanning/README.md).

### 5. Install Argo CD

Install Argo CD into its own namespace and wait for the server:

```powershell
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl rollout status deployment/argocd-server -n argocd --timeout=300s
```

The Argo CD UI and initial admin-password instructions are in [Open the Argo CD UI](#open-the-argo-cd-ui) below. Change the initial password after the first login.

### 6. Create the application namespace and database Secret

Create the namespace and Secret before syncing resources. The steps in [Create the database Secret](#create-the-database-secret) prompt for the MySQL root and application passwords. Use the same application password as the `sms_app` user in the database. If MySQL has already initialized its data volume, changing the Kubernetes Secret alone does not change the MySQL account password.

The Secret is deliberately not stored in Git or managed by Argo CD. Do not apply the placeholder [secret.yaml](manifests/secret.yaml) as production credentials.

### 7. Register the Argo CD Application and synchronize it

Apply the Application after cluster add-ons, storage, and the Secret are ready:

```powershell
kubectl apply -f argocd/application.yaml
kubectl get applications.argoproj.io -n argocd
kubectl get pods,pvc -n student-management
```

The Application tracks `main` and automatically syncs the Kustomize resources in `manifests/`. Keep `spec.source.directory` unset; Argo CD must detect `manifests/kustomization.yaml` and render it with Kustomize. Automatic self-healing is enabled and pruning is disabled to reduce the risk of removing stateful resources.

Wait for the MySQL PVC and pod to become healthy before diagnosing application startup. Check `kubectl describe pod` and pod Events if a container remains in `ContainerCreating` or enters a restart back-off.

### 8. Configure GitHub Actions, SonarQube, and S3 report access

Before pushing a source change or manually running the workflow, configure the GitHub repository variables and secrets listed in the [security-scanning guide](docs/security-scanning/README.md). These include the SonarQube URL/project, the GitHub OIDC AWS role/region/report bucket, SonarQube analysis and report tokens, and Docker Hub push credentials.

The SonarQube endpoint must be reachable by GitHub-hosted runners; use HTTPS for real CI runs so analysis tokens are not sent over plaintext HTTP. Confirm that GitHub OIDC trust is restricted to this repository and the intended `main` branch, and that its role can write only to the security-report S3 prefix.

### 9. Install Argo CD Image Updater

After the Argo CD Application exists, follow [Install Argo CD Image Updater](#install-argo-cd-image-updater). The updater needs a GitHub token with repository Contents write access to update the image tag in Git. Apply [image-updater.yaml](argocd/image-updater.yaml) only after the controller is installed and the `git-creds` Secret exists in namespace `argocd`.

### 10. Verify ingress and the end-to-end image update

Check that the Ingress has received an ALB address and the application Deployment is ready:

```powershell
kubectl get ingress,service -n student-management
kubectl rollout status deployment/student-management -n student-management --timeout=10m
kubectl get pods -n student-management -o wide
```

For an image-updater test, run the GitHub Actions workflow on `main` and wait for build, SonarQube, Trivy, and Docker Hub push to succeed. Image Updater should commit the new build tag to the repository; Argo CD should then sync and roll out that exact tag. The workflow ignores Image Updater's manifest-only commits so they do not trigger another image build. See [Image Updates](#image-updates) for the flow.

### 11. Enable and test scheduled MySQL backups

Only after MySQL is healthy, update the backup bucket/region and IRSA role placeholders, then follow [Scheduled MySQL Backups to S3](#scheduled-mysql-backups-to-s3) to apply the backup resources and run a manual test backup. Confirm the object appears in the private S3 backup prefix and periodically test restoring it.

## Argo CD Setup

The following sections provide the detailed commands for the runbook above. The Application in `argocd/application.yaml` uses the repository's HTTPS URL without credentials, so the repository must be publicly readable by Argo CD. For a private repository, register repository credentials with Argo CD before applying the Application.

### Install Argo CD

```powershell
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl rollout status deployment/argocd-server -n argocd --timeout=300s
```

### Create the database Secret

The `manifests/secret.yaml` file is excluded from Argo CD because it contains placeholders. Create the real Secret directly in the cluster before syncing the application. In PowerShell, enter the actual MySQL credentials when prompted:

```powershell
kubectl create namespace student-management --dry-run=client -o yaml | kubectl apply -f -
$root = Read-Host "MySQL root password" -AsSecureString
$db = Read-Host "MySQL application password" -AsSecureString
$rootPlain = [System.Net.NetworkCredential]::new('', $root).Password
$dbPlain = [System.Net.NetworkCredential]::new('', $db).Password
kubectl create secret generic student-management-db-secret -n student-management --from-literal="MYSQL_ROOT_PASSWORD=$rootPlain" --from-literal="DB_PASSWORD=$dbPlain" --dry-run=client -o yaml | kubectl apply -f -
Remove-Variable root,db,rootPlain,dbPlain
```

Use the same database password that the existing `sms_app` MySQL account has. If the Secret already exists, update it deliberately rather than running the create command again. Updating a Secret does not change passwords stored inside an initialized MySQL database.

### Install Argo CD Image Updater

Image Updater watches Docker Hub for ordered build tags containing the commit SHA and commits the selected tag to `manifests/kustomization.yaml`. It needs write access to the GitHub repository; create a fine-grained GitHub token with **Contents: Read and write** permission and permission to push to `main`.

Install Image Updater and create its Git credential Secret. Enter the token when prompted; do not commit it or put it in a script:

```powershell
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj-labs/argocd-image-updater/stable/config/install.yaml
$gitUser = Read-Host "GitHub username"
$token = Read-Host "GitHub token" -AsSecureString
$tokenPlain = [System.Net.NetworkCredential]::new('', $token).Password
kubectl create secret generic git-creds -n argocd --from-literal="username=$gitUser" --from-literal="password=$tokenPlain" --dry-run=client -o yaml | kubectl apply -f -
Remove-Variable gitUser,token,tokenPlain
kubectl apply -f argocd/image-updater.yaml
```

The repository must allow this token to push directly to `main`. If branch protection disallows direct pushes, configure a write branch and merge process before enabling Git write-back.

### Register and Sync the Application

```powershell
kubectl apply -f argocd/application.yaml
kubectl get applications.argoproj.io -n argocd
kubectl get pods -n student-management
```

The Application automatically syncs manifest changes and self-heals drift. Automatic pruning is disabled to avoid deleting stateful MySQL resources when manifests are removed. The MySQL Secret is not managed by Argo CD.

The Application uses the `manifests/kustomization.yaml` file to render resources with Kustomize. Do not configure `spec.source.directory` for this Application; directory mode makes Argo CD interpret `kustomization.yaml` as a Kubernetes resource instead of a build file.

### Open the Argo CD UI

In a separate PowerShell window, forward the Argo CD server port:

```powershell
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

Open `https://localhost:8080`, accept the local certificate warning, and sign in as `admin`. Retrieve the initial password with:

```powershell
$encoded = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($encoded))
```

Change the initial admin password after signing in. The UI port-forward only lasts while the command is running.

### Image Updates

On every successful push to `main`, GitHub Actions builds and scans the image, then pushes a full commit-SHA tag, a `build-<run-number>-<commit-sha>` tag, and `latest` to Docker Hub. Argo CD Image Updater watches only the ordered build tags and writes the highest run-number tag to `manifests/kustomization.yaml`. This avoids depending on Docker Hub's image-creation metadata to choose the newest tag. Argo CD detects the Git change and rolls out that exact image. The workflow ignores commits that only update this Kustomize file, preventing an image-update commit from starting another build. The `latest` tag is retained for compatibility but is not used to trigger deployments.

### Scheduled MySQL Backups to S3

The backup resources are in `backups/`, outside the Argo CD application's `manifests/` path. They use a CronJob with a MySQL `mysqldump` init container, then upload the completed dump to a private S3 bucket using the EKS IAM role for service accounts (IRSA). A DaemonSet is not used: it would run on every node and could create duplicate database backups.

Before applying, configure IRSA for the cluster's OIDC provider. Create an IAM role whose trust policy allows `sts:AssumeRoleWithWebIdentity` only for the service account subject `system:serviceaccount:student-management:student-management-backup` and audience `sts.amazonaws.com`. Attach a policy scoped to the backup prefix, allowing `s3:PutObject` and `s3:AbortMultipartUpload` on `arn:aws:s3:::YOUR_BUCKET/student-management/mysql/*`. Keep S3 Block Public Access enabled. Replace the bucket name and IAM role ARN placeholders in `backups/backup-config.yaml` and `backups/backup-serviceaccount.yaml`; adjust `AWS_REGION` if necessary.

Apply the backup resources after the database Secret and MySQL service are available:

```powershell
kubectl apply -f backups/backup-config.yaml
kubectl apply -f backups/backup-serviceaccount.yaml
kubectl apply -f backups/backup-cronjob.yaml
```

The schedule is daily at 02:00 UTC. The dump uses the existing `student-management-config` and `student-management-db-secret`; the database user must have permission to dump the application schema, routines, triggers, and events. The Pod writes a temporary uncompressed SQL dump to an `emptyDir` limited to 5 GiB, then uploads it with S3 server-side encryption (`AES256`). Increase that limit if your database dump may exceed 5 GiB.

Trigger a test backup immediately and inspect its result:

```powershell
$job = "student-management-backup-manual-$(Get-Date -Format yyyyMMddHHmmss)"
kubectl create job --from=cronjob/student-management-mysql-backup $job -n student-management
kubectl wait --for=condition=complete "job/$job" -n student-management --timeout=30m
kubectl logs "job/$job" -n student-management
aws s3 ls s3://YOUR_BUCKET/student-management/mysql/
```

The CronJob is not itself a restore strategy. Keep an S3 lifecycle/retention policy and periodically test restoring a dump into a separate database before relying on these backups.

### Pipeline Flow

```text
docs/videos/jenkins-cicd-demo.mp4
```

---

# 📸 Application Screenshots

## 🔐 Login

![Login](docs/screenshots/login.png)

## 📊 Dashboard

![Dashboard](docs/screenshots/dashboard.png)

## 👨‍🎓 Student List

![Student List](docs/screenshots/student-list.png)

## ➕ Add Student

![Add Student](docs/screenshots/add-student.png)

---

# 🎥 Project Demo

## 🎥 Application Demo

[![Student Management System Demo](https://img.youtube.com/vi/Frmffy9lOsI/maxresdefault.jpg)](https://youtu.be/Frmffy9lOsI)

Click the image above to view the complete application demo.

The application demo demonstrates:

- Login
- Dashboard
- Student management
- CRUD operations
- Search
- Application navigation

---



# ☁️ AWS Deployment

The Jenkins pipeline uses an SSH credential:

```text
student-management-app-ssh
```

The application server receives the generated JAR through SCP.

The deployment location is:

```text
AWS VPC
│
├── Public Subnet
│   ├── Jenkins EC2
│   └── Application EC2
│
└── Database Subnets
    └── Amazon RDS MySQL
```

### Jenkins EC2

Jenkins runs on an AWS EC2 instance and is responsible for:

- Pulling source code
- Running Maven builds
- Running tests
- Creating the JAR
- Deploying the application
- Verifying deployment

### Application EC2

The application server:

- Runs Ubuntu Linux
- Runs Java 21
- Hosts the Spring Boot JAR
- Uses systemd for process management
- Exposes the Spring Boot application on port `8080`
- Connects to Amazon RDS through MySQL port `3306`

### Amazon RDS

Amazon RDS MySQL provides the persistent database for the application.

```text
Application EC2
      |
      | TCP 3306
      v
Amazon RDS MySQL
      |
      v
   studentdb
```

The RDS security group is designed to allow MySQL traffic from the Application Server security group rather than opening port `3306` to the entire internet.

---

# 🗄️ Database Configuration

The Spring Boot application supports environment variables for database configuration:

```properties
spring.datasource.url=jdbc:mysql://${DB_HOST:localhost}:${DB_PORT:3306}/${DB_NAME:studentdb}?useSSL=false&serverTimezone=UTC&allowPublicKeyRetrieval=true

spring.datasource.username=${DB_USER:root}

spring.datasource.password=${DB_PASSWORD:root}
```

Production deployment uses environment variables rather than committing database credentials into the source code.

Example:

```text
DB_HOST=<RDS_ENDPOINT>
DB_PORT=3306
DB_NAME=studentdb
DB_USER=<RDS_USERNAME>
DB_PASSWORD=<RDS_PASSWORD>
```

> 🔐 **Never commit real database passwords, private keys, or other secrets to GitHub.**

---

# ⚙️ Application Service

The application runs as a Linux `systemd` service.

Service:

```text
student-management.service
```

Application directory:

```text
/opt/student-management
```

JAR:

```text
/opt/student-management/student-management.jar
```

Application port:

```text
8080
```

The service runs the Spring Boot JAR using Java 21 and automatically restarts if the process exits.

Useful commands:

```bash
sudo systemctl status student-management
```

```bash
sudo systemctl restart student-management
```

```bash
sudo journalctl -u student-management -n 50 --no-pager
```

---

# 🔌 REST API

The application provides REST endpoints for student management.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/students` | List students |
| GET | `/api/students/{id}` | Get student |
| POST | `/api/students` | Create student |
| PUT | `/api/students/{id}` | Update student |
| DELETE | `/api/students/{id}` | Delete student |

Example:

```text
GET /api/students
```

---

# 📁 Project Structure

```text
Student-management/
│
├── docs/
│   ├── architecture/
│   │   └── student-management-architecture.png
│   │
│   ├── screenshots/
│   │   ├── login.png
│   │   ├── dashboard.png
│   │   ├── student-list.png
│   │   ├── add-student.png
│   │   └── application-demo-thumbnail.png
│   │
│   └── videos/
│       ├── application-demo.mp4
│       └── jenkins-cicd-demo.mp4
│
├── src/
│   ├── main/
│   │   ├── java/
│   │   └── resources/
│   │
│   └── test/
│
├── Jenkinsfile
├── pom.xml
├── README.md
└── .gitignore
```

---

# 🔐 Security Considerations

The project follows several basic security practices:

- 🔑 SSH access should be restricted to trusted IP addresses
- 🛡️ AWS Security Groups control inbound traffic
- 🔒 Database access is restricted to the application server
- 🚫 Database should not be publicly accessible
- 🔐 Production credentials should be stored outside source code
- 🔄 SSH keys should be managed securely
- 🗝️ Database credentials should be rotated periodically

For production environments, AWS Secrets Manager or SSM Parameter Store can be used for centralized secret management.

---

# 🛠️ Useful Troubleshooting Commands

### Check application service

```bash
sudo systemctl status student-management --no-pager
```

### Check application logs

```bash
sudo journalctl -u student-management -n 100 --no-pager
```

### Check port 8080

```bash
sudo ss -lntp | grep 8080
```

### Test the application locally on EC2

```bash
curl -I http://localhost:8080
```

Expected application response may redirect to the login page.

### Test RDS connectivity

```bash
nc -zv <RDS-ENDPOINT> 3306
```

---

# 🎯 What I Learned From This Project

This project helped me gain practical experience in:

- AWS EC2 provisioning and configuration
- Amazon RDS MySQL
- VPC and Security Groups
- Linux server administration
- Java application deployment
- Maven build automation
- Jenkins CI/CD
- Git and GitHub
- SSH key-based authentication
- SCP-based deployment
- systemd service management
- Application troubleshooting
- Database connectivity troubleshooting
- CI/CD pipeline debugging

---

# 🚀 Future Improvements

Possible improvements for the project:

- [ ] HTTPS using SSL/TLS
- [ ] AWS Application Load Balancer
- [ ] Auto Scaling
- [ ] CloudWatch monitoring
- [ ] AWS Secrets Manager
- [ ] Blue/Green deployment
- [ ] Automated rollback
- [ ] Infrastructure as Code with Terraform
- [ ] Jenkins webhook-based automatic deployment
- [ ] Automated integration testing

---

# 📊 Project Highlights

```text
┌──────────────────────────────────────────┐
│        STUDENT MANAGEMENT SYSTEM         │
├──────────────────────────────────────────┤
│                                          │
│  ☁️ AWS Deployment                       │
│  🔄 Jenkins CI/CD                        │
│  ☕ Spring Boot                           │
│  🗄️ MySQL RDS                            │
│  🐧 Linux / Ubuntu                       │
│  🔐 SSH/SCP Deployment                   │
│  ⚙️ systemd                              │
│  📦 Maven                                │
│                                          │
└──────────────────────────────────────────┘
```

---

# 👨‍💻 Author

## Pritesh Biradar

AWS / DevOps enthusiast focused on building practical cloud infrastructure, CI/CD pipelines, and production-style deployments.

### GitHub

[![GitHub](https://img.shields.io/badge/GitHub-PriteshBiradar-black?logo=github)](https://github.com/PriteshBiradar)

### Portfolio

[![Portfolio](https://img.shields.io/badge/Portfolio-Visit-blue?logo=googlechrome)](https://priteshbiradar.github.io/Pritesh_Biradar.github.io/)

---

## ⭐ If you found this project useful

Feel free to ⭐ the repository and explore the implementation.

---

### 🚀 Learning • Building • Automating • Growing
**Built by MANGESH CHAUHAN**