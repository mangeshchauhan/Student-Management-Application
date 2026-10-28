# EKS Dev Environment

This guide deploys the `Dev` Git branch into an isolated Kubernetes namespace using a separate MySQL EBS volume, a separate Argo CD Application, and Dev-only Docker image tags. Production continues to use the `main` branch, `student-management` namespace, and production image tags.

The Dev EBS volume ID and Availability Zone are intentionally placeholders until you create a dedicated volume. **Do not use the production EBS volume. Do not deploy the Dev Application until both placeholders have been replaced with the actual Dev volume details.**

Dev Ingress is intentionally not configured yet. The application is reachable through a temporary `kubectl port-forward` for testing.

## What is isolated

| Resource | Production | Dev |
|---|---|---|
| Git branch | `main` | `Dev` |
| Argo CD Application | `student-management` | `student-management-dev` |
| Kubernetes namespace | `student-management` | `student-management-dev` |
| MySQL EBS PV | Production PV | Dedicated Dev PV |
| Docker image update tags | `build-<run>-<sha>` | `dev-build-<run>-<sha>` |
| Image tracking tag | `latest` | `dev-latest` |
| SonarQube project key | `SONAR_PROJECT_KEY` | `SONAR_PROJECT_KEY_DEV` |
| Ingress | Production ALB config | Not configured |

Both environments use the same Docker Hub repository, but the distinct image tag patterns keep the production and Dev Image Updaters from selecting each other's builds. The Dev overlay deliberately does not include the production Ingress, PersistentVolume, PersistentVolumeClaim, or cluster-wide StorageClass.

## 1. Confirm EKS context and prerequisites

Use the EKS cluster where Argo CD is installed. Confirm the selected context and worker nodes:

```powershell
kubectl config current-context
kubectl get nodes -o wide
```

The Amazon EBS CSI driver must be installed and healthy. Argo CD and the Argo CD Image Updater controller must run in the `argocd` namespace.

## 2. Create and configure a dedicated Dev EBS volume

Create a new, empty, encrypted EBS volume of at least **15 GiB** in the same AWS Region and Availability Zone as a worker node. Do not select or attach the production volume. Record the new volume ID and Availability Zone.

Edit `manifests/overlays/dev/mysql-pv.yaml` and replace:

- `REPLACE_WITH_DEV_EBS_VOLUME_ID` with the Dev volume ID (for example, `vol-0123456789abcdef0`).
- `REPLACE_WITH_DEV_EBS_AVAILABILITY_ZONE` with its exact Availability Zone (for example, `us-east-1a`).

The static PV and PVC use `storageClassName: ""` and bind by the explicit PV name. The volume must be formatted with a filesystem supported by the EBS CSI driver. Keep the PV reclaim policy set to `Retain` unless you have an intentional data-destruction policy.

Validate that placeholders have been replaced before deployment:

```powershell
Select-String -Path .\manifests\overlays\dev\mysql-pv.yaml -Pattern 'REPLACE_WITH'
kubectl kustomize .\manifests\overlays\dev
```

The first command must return no matches. Do not apply a rendered manifest while a placeholder remains.

## 3. Create the Dev database Secret

Create a Secret in the Dev namespace. Use a Dev-only MySQL root password and application password; do not reuse production credentials:

```powershell
kubectl create namespace student-management-dev --dry-run=client -o yaml | kubectl apply -f -
$root = Read-Host "Dev MySQL root password" -AsSecureString
$db = Read-Host "Dev MySQL application password" -AsSecureString
$rootPlain = [System.Net.NetworkCredential]::new('', $root).Password
$dbPlain = [System.Net.NetworkCredential]::new('', $db).Password
kubectl create secret generic student-management-db-secret -n student-management-dev --from-literal="MYSQL_ROOT_PASSWORD=$rootPlain" --from-literal="DB_PASSWORD=$dbPlain" --dry-run=client -o yaml | kubectl apply -f -
Remove-Variable root,db,rootPlain,dbPlain
```

The Secret is namespace-local and is not committed to Git. MySQL initializes its user and password when it first initializes the data directory; changing this Secret later does not automatically change the password stored in the database.

## 4. Configure the Dev SonarQube project

Create a separate SonarQube project and configure the GitHub Actions repository variable `SONAR_PROJECT_KEY_DEV` with its project key. The recommended project key is `Student-Management-Dev`. This avoids publishing Dev analysis as the production project's main-branch analysis.

The workflow continues to use the existing `SONAR_HOST_URL` and `SONAR_TOKEN`. Configure the token account with analysis permission for both SonarQube projects.

## 5. Validate the branch and publish the initial Dev image

Create feature branches from `Dev`, using names such as `Feature/CR2222`. Open pull requests from `Feature/*` into `Dev`; when promoting a tested release, open a pull request from `Dev` into `main`. The Branch Validation workflow enforces these source/target combinations only. Require its `Validate source branch` check in GitHub branch rules before merging.

After a pull request is merged, CI/CD runs on pushes to `main` and `Dev`. It runs Maven tests and SonarQube analysis for the selected branch, then builds and scans the image. A successful `Dev` run pushes:

- `mangeshc225/student-management:<commit-sha>`
- `mangeshc225/student-management:dev-build-<10-digit-run-number>-<commit-sha>`
- `mangeshc225/student-management:dev-latest`

Run the workflow from the GitHub Actions tab with branch `Dev`, or push a change to `Dev`. Wait for the workflow to complete successfully before deploying; the `dev-latest` tag must exist before Kubernetes starts the application container.

Only the exact `main` branch publishes the production `build-*` and `latest` tags. Manual workflow dispatch from other branches is blocked from running the build and image-publish jobs.

## 6. Register the Dev Argo CD Application

From the repository root, apply the Dev Application:

```powershell
kubectl apply -f .\argocd\application-dev.yaml
kubectl get application student-management-dev -n argocd
kubectl get pv,pvc -n student-management-dev
kubectl get pods -n student-management-dev
```

The Application tracks branch `Dev` and path `manifests/overlays/dev`. Its resources are isolated in `student-management-dev`; the overlay does not include an Ingress, so no ALB hostname is created for Dev.

## 7. Configure Image Updater for Dev

Create or reuse `git-creds` in namespace `argocd` with a GitHub token that has repository Contents write permission and can push to `Dev`. The existing production Image Updater uses the same Secret name, so it can be reused if that token already has the required access.

Apply the Dev updater:

```powershell
kubectl apply -f .\argocd\image-updater-dev.yaml
kubectl get imageupdater -n argocd
kubectl logs deployment/argocd-image-updater-controller -n argocd --since=15m
```

The Dev updater watches only `dev-build-*` image tags and writes the selected tag to `manifests/overlays/dev/kustomization.yaml` on branch `Dev`. The production updater continues to watch only `build-*` tags and writes to the production Kustomize file on `main`.

## 8. Verify the Dev application

Check the namespace resources and wait for both MySQL and the application:

```powershell
kubectl rollout status deployment/mysql -n student-management-dev --timeout=10m
kubectl rollout status deployment/student-management -n student-management-dev --timeout=10m
kubectl get pods,pvc -n student-management-dev -o wide
kubectl get deployment student-management -n student-management-dev -o jsonpath="{.spec.template.spec.containers[0].image}"
```

For temporary access from your workstation:

```powershell
kubectl port-forward service/student-management -n student-management-dev 8081:8080
```

Open `http://localhost:8081`. The port-forward must remain running. Before exposing Dev through an ALB, decide whether it should be public or internal and provide the DNS hostname and (for HTTPS) ACM certificate details.

## 9. Keep production and Dev changes separate

- Merge feature work into `Dev` through pull requests, then promote tested changes from `Dev` to `main` through a separate pull request.
- Keep production credentials and the production EBS volume out of the Dev namespace.
- Do not use the production Argo CD Application or production ImageUpdater resource to manage the Dev overlay.
- The production MySQL backup manifests are not configured for Dev. Do not apply them into the Dev namespace without creating a separate S3 prefix, IAM trust for the Dev backup service account, and a Dev-specific backup configuration.
