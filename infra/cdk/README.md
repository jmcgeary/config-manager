Config Manager Demo — ECS Fargate (Milestone 7)

Overview
- Provisions a public demo environment on AWS using ECS Fargate with a single task that runs 4 containers (3× etcd + config-service) behind an internet-facing Application Load Balancer.
- All infra is codified with AWS CDK (Python), matching DEMO_SPEC.md and MILESTONES.md Milestone 7.

What this stack creates
- VPC (public subnets, no NAT) and security groups
- ECS Cluster and Fargate Service (desired count 1)
- Task Definition with 4 containers:
  - etcd1 (2379/2380), etcd2 (32379/32380), etcd3 (42379/42380)
  - config-service (8080)
- CloudWatch Log Group for container logs
- Application Load Balancer + HTTP listener on 80 + Target Group (health check /health)
- Stack output with the ALB DNS name

Defaults and notes
- Fargate Service assigns a public IP to allow outbound internet access without NAT. Ingress is still restricted to ALB→task on port 8080 only.
- etcd data uses an ephemeral volume in the task (no persistence). For persistence, extend the stack to use EFS.
- Image source options:
  - Quick (dev/demo): build from repo Dockerfile using CDK assets (`imageMode=asset`). Requires local Docker when deploying.
  - Best practice (recommended): push image to ECR via CI, then deploy with `imageMode=ecr` and a versioned tag.

Project layout
- app.py — CDK app entrypoint
- cdk.json — Context and app command
- requirements.txt — CDK dependencies
- stacks/ecs_fargate_stack.py — Main stack implementation

Parameters via CDK context
- imageMode: one of [asset, ecr]. Default: asset
- ecrRepoName: name of ECR repo when imageMode=ecr (default: config-service)
- ecrTag: tag when imageMode=ecr (default: latest)
- cpu: task CPU units (default: 1024)
- memoryMiB: task memory MiB (default: 2048)
- logGroupName: CloudWatch Logs group (default: /ecs/config-manager-demo)

Usage
1) Prereqs
   - AWS account and credentials set in your terminal (AWS_PROFILE/AWS_REGION)
   - Node.js and Python 3.10+
   - Install AWS CDK v2 (npm i -g aws-cdk)

2) Create and activate a virtualenv
   - python -m venv .venv && source .venv/bin/activate

3) Install dependencies
   - pip install -r requirements.txt

4) Bootstrap your AWS environment (once per account/region)
   - cdk bootstrap

5a) Use CDK assets to build/push the config-service image automatically
   - cdk deploy

5b) Or use a pre-pushed ECR image (recommended)
   - cdk deploy -c imageMode=ecr -c ecrRepoName=<your-repo> -c ecrTag=<tag>

Fast path (one-liner)
- From repo root, with Docker running and AWS creds loaded:
  - ./scripts/deploy_demo_asset.sh

Publish to ECR (options)
- Manual script (requires AWS CLI and Docker):
  - ./scripts/build_and_push_ecr.sh <aws-account-id> <region> <repo-name> <tag>
- GitHub Actions (recommended):
  - Configure an AWS IAM role for GitHub OIDC and set repo secrets: AWS_ACCOUNT_ID, AWS_REGION, ECR_REPOSITORY.
  - On pushes to main or tags, CI will build and push image to ECR with both `<git-sha>` and `latest` tags.

Outputs
- AlbDnsName: copy this value and open http://<AlbDnsName>/ to use the demo.

Cleanup
- cdk destroy
