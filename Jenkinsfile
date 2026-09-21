pipeline {
agent any

options {
    timestamps()
    disableConcurrentBuilds()
    skipDefaultCheckout(true)
}

parameters {
    string(
        name: 'APP_SERVER_HOST',
        defaultValue: '',
        description: 'Application EC2 private IP or DNS name',
        trim: true
    )

    string(
        name: 'APP_SERVER_USER',
        defaultValue: 'ubuntu',
        description: 'SSH user on the application EC2 instance',
        trim: true
    )
}

environment {
    APP_DIRECTORY = '/opt/student-management'
    SSH_CREDENTIALS_ID = 'student-management-app-ssh'
}

stages {

    stage('Checkout') {
        steps {
            checkout scm
        }
    }

    stage('Build and Test') {
        steps {
            sh 'mvn -B clean verify'
        }
    }

    stage('Archive Artifact') {
        steps {
            archiveArtifacts(
                artifacts: 'target/*.jar',
                fingerprint: true
            )
        }
    }

    stage('Deploy to Application Server') {
        steps {

            script {
                if (!params.APP_SERVER_HOST?.trim()) {
                    error('APP_SERVER_HOST must be provided for deployment')
                }
            }

            sshagent(credentials: [env.SSH_CREDENTIALS_ID]) {

                sh '''
                    set -eu

                    JAR_FILE=$(find target -maxdepth 1 -type f -name "*.jar" ! -name "*-plain.jar" | head -n 1)

                    if [ -z "$JAR_FILE" ]; then
                        echo "ERROR: Spring Boot JAR not found."
                        exit 1
                    fi

                    echo "Deploying: $JAR_FILE"

                    remote_jar="/tmp/student-management-${BUILD_NUMBER}.jar"

                    ssh_options="-o BatchMode=yes -o StrictHostKeyChecking=no"

                    scp ${ssh_options} "$JAR_FILE" \
                        "${APP_SERVER_USER}@${APP_SERVER_HOST}:${remote_jar}"

                    ssh ${ssh_options} \
                         "${APP_SERVER_USER}@${APP_SERVER_HOST}" \
                         "sudo install -o ubuntu -g ubuntu -m 0644 '${remote_jar}' '${APP_DIRECTORY}/student-management.jar' && \
                         rm -f '${remote_jar}' && \
                         sudo systemctl restart student-management && \
                        echo 'Waiting for application to start...' && \
                         sleep 15 && \
                        sudo systemctl is-active --quiet student-management && \
                         echo 'Checking application health...' && \
                         curl -fsS http://localhost:8080/login > /dev/null"

                    echo "Deployment and health check successful."
                '''
            }
        }
    }
}

post {
    success {
        echo 'Student Management build and deployment completed successfully.'
    }

    failure {
        echo 'Student Management pipeline failed.'
    }
}

}