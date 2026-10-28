# EKS Deployment (Docker, Argo CD, and Image Updater)

This guide covers the containerized Kubernetes deployment to Amazon EKS. It assumes the EKS cluster and worker nodes already exist. For the legacy Jenkins/JAR deployment to EC2, use the [EC2 deployment guide](../ec2/README.md).

The pull-request workflow enforces `Feature/* → Dev → main` and validates Maven tests and Trivy scans before merge. The CI/CD workflow builds and scans images from `main` and `Dev`, then pushes branch-specific image tags to Docker Hub. SonarQube CSV report generation and Trivy CSV/S3 exports are currently disabled. Argo CD synchronizes production from `manifests/overlays/prod` on `main` and Dev from `manifests/overlays/dev` on `Dev`. For isolated Dev resources, use the [EKS Dev guide](./dev/README.md).

## Prerequisites

- AWS CLI, `kubectl`, Git, and PowerShell on the operator workstation.
- IAM access to the EKS cluster.
- A publicly readable GitHub repository (the Argo CD Application uses unauthenticated HTTPS Git), or Argo CD repository credentials configured for a private repository.
- Docker Hub image repository matching the image name in `argocd/image-updater.yaml` and `manifests/overlays/prod/kustomization.yaml`.
- A static EBS volume dedicated to MySQL, as described in Step 3.
- A private S3 bucket for database backups and security reports.
- GitHub Actions variables/secrets and SonarQube configuration described in [Security Scanning](../../security-scanning/README.md).

Replace all placeholders with your own values. Never commit credentials or tokens.

## 1. Connect to the EKS cluster

Configure AWS credentials for a cluster-authorized IAM identity, then set the kubeconfig context:

```powershell
aws eks update-kubeconfig --region YOUR_AWS_REGION --name YOUR_CLUSTER_NAME
kubectl config current-context
kubectl get nodes -o wide
```

Confirm the expected cluster context and Ready worker nodes before applying resources.

## 2. Install required EKS add-ons

Install and configure:

1. **Amazon EBS CSI driver** with the required IAM permissions so Kubernetes can attach the MySQL volume.
2. **AWS Load Balancer Controller** with the required IAM permissions so the Ingress can create an ALB.

Follow the AWS guides for [EBS CSI](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html) and the [AWS Load Balancer Controller](https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html). Verify controller health and available storage classes:

```powershell
kubectl get pods -n kube-system
kubectl get storageclass
```

Do not continue until both add-ons are configured and healthy.

## 3. Configure MySQL EBS storage

This repository currently uses a **static EBS PersistentVolume**. Edit `manifests/overlays/prod/mysql-pv.yaml` before deployment:

- Set `volumeHandle` to the ID of the EBS volume dedicated to this database.
- Set the node-affinity availability zone to the volume's actual availability zone.
- Ensure the volume and EKS cluster are in the same AWS region.
- Never reuse a volume containing data you do not intend to retain.

The PV and PVC currently specify `gp2`; both must use the same storage class name, and that class must exist in the cluster. `manifests/overlays/prod/storageclass.yaml` also defines `ebs-gp3-retain`, but defining that class does not automatically change the PV/PVC. If choosing another storage class, update the PV and claim consistently before applying.

## 4. Create the S3 bucket and IAM roles

Create a private bucket with S3 Block Public Access enabled. Use separate least-privilege roles for EKS backups and GitHub Actions reports.

### EKS backup role

Configure IRSA for the cluster OIDC provider. Restrict the role trust policy to service account subject:

```text
system:serviceaccount:student-management:student-management-backup
```

Grant only the permissions required to upload backup objects, scoped to:

```text
arn:aws:s3:::YOUR_BUCKET/student-management/mysql/*
```

Update the bucket, region, and role ARN in `backups/backup-config.yaml` and `backups/backup-serviceaccount.yaml`.

### GitHub Actions report role

Configure the GitHub OIDC provider `token.actions.githubusercontent.com`. Restrict the trust policy audience to `sts.amazonaws.com` and subject to:

```text
repo:mangeshchauhan/Student-Management-Application:ref:refs/heads/main
```

Grant `s3:PutObject` only for:

```text
arn:aws:s3:::YOUR_BUCKET/security-reports/mangeshchauhan/Student-Management-Application/*
```

Set the role ARN, region, and report bucket as GitHub Actions repository variables in Step 10. Do not use the EKS IRSA role for GitHub Actions. Detailed setup guidance is in the [security-scanning guide](../../security-scanning/README.md).

## 5. Install Argo CD

Install Argo CD in its own namespace and wait for the server deployment:

```powershell
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl rollout status deployment/argocd-server -n argocd --timeout=300s
```

To open the UI, port-forward from a separate PowerShell window:

```powershell
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

Browse to `https://localhost:8080`. Retrieve the initial admin password with:

```powershell
$encoded = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($encoded))
```

Change the initial password after logging in. The port-forward only works while its command is running.

## 6. Create the database Secret

Create the application namespace and a real Secret before Argo CD syncs the workloads. Run from the repository root in PowerShell:

```powershell
kubectl create namespace student-management --dry-run=client -o yaml | kubectl apply -f -
$root = Read-Host "MySQL root password" -AsSecureString
$db = Read-Host "MySQL application password" -AsSecureString
$rootPlain = [System.Net.NetworkCredential]::new('', $root).Password
$dbPlain = [System.Net.NetworkCredential]::new('', $db).Password
kubectl create secret generic student-management-db-secret -n student-management --from-literal="MYSQL_ROOT_PASSWORD=$rootPlain" --from-literal="DB_PASSWORD=$dbPlain" --dry-run=client -o yaml | kubectl apply -f -
Remove-Variable root,db,rootPlain,dbPlain
```

The `manifests/secret.yaml` file contains placeholders and is intentionally not part of the Kustomize resources. Use the same application password configured for the initialized MySQL account; changing a Secret does not change a password stored inside an existing database volume.

## 7. Register and sync the Argo CD Application

Apply the Application from the repository root:

```powershell
kubectl apply -f argocd/application.yaml
kubectl get applications.argoproj.io -n argocd
kubectl get pods,pvc -n student-management
```

The production Application tracks the `main` branch and `manifests/overlays/prod` path. Do not add `spec.source.directory`; Argo CD must detect the Kustomize file in that path. The Secret is not managed by Argo CD. Automated sync and self-healing are enabled; pruning is disabled to reduce the risk of removing stateful resources.

Wait for the MySQL PVC to bind and the MySQL pod to become Ready. Useful checks:

```powershell
kubectl get pv,pvc -n student-management
kubectl describe pvc mysql-data -n student-management
kubectl get pods -n student-management -o wide
kubectl get events -n student-management --sort-by=.lastTimestamp
```

If MySQL or the application pod is in `ContainerCreating` or restart back-off, inspect `kubectl describe pod` and logs before proceeding.

## 8. Configure CI/CD and branch-specific SonarQube projects

Configure GitHub Actions variables and secrets as listed in the [security-scanning guide](../../security-scanning/README.md), including:

- Variables: `SONAR_HOST_URL`, `SONAR_PROJECT_KEY`, `SONAR_PROJECT_KEY_DEV`.
- Secrets: `SONAR_TOKEN`, `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`.

Use HTTPS for SonarQube when GitHub-hosted runners connect. `SONAR_PROJECT_KEY_DEV` must identify a separate SonarQube project for the Dev branch. CSV report generation and report S3 uploads are currently disabled.

On successful scans, CI pushes branch-specific image tags: production `build-*`/`latest` tags from `main`, and `dev-build-*`/`dev-latest` tags from `Dev`. A failed quality gate or blocking Trivy result prevents the image push.

## 9. Install Argo CD Image Updater

Install Image Updater into the same `argocd` namespace:

```powershell
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj-labs/argocd-image-updater/stable/config/install.yaml
kubectl rollout status deployment/argocd-image-updater-controller -n argocd --timeout=300s
```

Create a GitHub bot identity and fine-grained token with repository **Contents: Read and write** access to the required branches. Store the token in a Kubernetes Secret (do not put it in a file committed to Git):

```powershell
$gitUser = Read-Host "GitHub username"
$token = Read-Host "GitHub token" -AsSecureString
$tokenPlain = [System.Net.NetworkCredential]::new('', $token).Password
kubectl create secret generic git-creds -n argocd --from-literal="username=$gitUser" --from-literal="password=$tokenPlain" --dry-run=client -o yaml | kubectl apply -f -
Remove-Variable gitUser,token,tokenPlain
```

If branch rules require pull requests, add the bot identity as the narrowly scoped bypass actor for the Image Updater write-back; do not allow routine human direct pushes. Apply the ImageUpdater resource:

```powershell
kubectl apply -f argocd/image-updater.yaml
kubectl get imageupdater -n argocd
```

The configuration watches ordered `build-<run-number>-<commit-sha>` Docker Hub tags and uses the `kustomization` Git write-back target to update `manifests/overlays/prod/kustomization.yaml`. The CI workflow ignores image-updater-only changes to that file to avoid triggering another image build.

## 10. Verify the application and image update

Check the ALB Ingress address, pod health, and deployment rollout:

```powershell
kubectl get ingress,service -n student-management
kubectl rollout status deployment/student-management -n student-management --timeout=10m
kubectl get pods -n student-management -o wide
```

The Ingress currently configures HTTP on port 80. For production HTTPS, configure an ACM certificate and the corresponding AWS Load Balancer Controller HTTPS/listener annotations before exposing the application.

To test image updates, run the GitHub Actions workflow on `main`. After the build, security scans, and Docker Hub push pass:

1. Image Updater should commit the new image tag to `manifests/overlays/prod/kustomization.yaml`.
2. Argo CD should detect and sync that Git commit.
3. Kubernetes should roll out the new image.

Check controller logs and live image state:

```powershell
kubectl logs deployment/argocd-image-updater-controller -n argocd --since=15m
kubectl get application student-management -n argocd
kubectl get deployment student-management -n student-management -o jsonpath="{.spec.template.spec.containers[0].image}"
```

## 11. Configure and test scheduled MySQL backups

After MySQL is Ready, replace the bucket, region, and IAM role placeholders in `backups/backup-config.yaml` and `backups/backup-serviceaccount.yaml`. Confirm the EKS backup role has IRSA trust for the exact service account and can write only to the MySQL backup prefix.

Apply the resources:

```powershell
kubectl apply -f backups/backup-config.yaml
kubectl apply -f backups/backup-serviceaccount.yaml
kubectl apply -f backups/backup-cronjob.yaml
```

The schedule is daily at 02:00 UTC. The CronJob dumps the application database using the existing database config and Secret, then uploads the SQL dump with SSE-S3 (`AES256`). Its temporary `emptyDir` is limited to 5 GiB; increase this if the dump may be larger.

Run a manual test and verify the S3 object:

```powershell
$job = "student-management-backup-manual-$(Get-Date -Format yyyyMMddHHmmss)"
kubectl create job --from=cronjob/student-management-mysql-backup $job -n student-management
kubectl wait --for=condition=complete "job/$job" -n student-management --timeout=30m
kubectl logs "job/$job" -n student-management
aws s3 ls s3://YOUR_BUCKET/student-management/mysql/
```

Configure retention/lifecycle rules and periodically test restoring a dump to a separate database. A successful backup job alone does not verify that a restore will work.
