from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
)
from constructs import Construct
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticloadbalancingv2 as elbv2,
    aws_logs as logs,
    aws_ecr as ecr,
    aws_ecr_assets as ecr_assets,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)


class EcsFargateStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Context parameters with defaults
        image_mode = self.node.try_get_context("imageMode") or "asset"  # 'asset' or 'ecr'
        ecr_repo_name = self.node.try_get_context("ecrRepoName") or "config-service"
        ecr_tag = self.node.try_get_context("ecrTag") or "latest"
        cpu = int(self.node.try_get_context("cpu") or 1024)
        memory_mib = int(self.node.try_get_context("memoryMiB") or 2048)
        arch_ctx = (self.node.try_get_context("cpuArch") or "x86_64").lower()
        log_group_name = self.node.try_get_context("logGroupName") or "/ecs/config-manager-demo"

        # VPC — public subnets, no NAT (tasks get public IP for egress)
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        # Security Groups
        alb_sg = ec2.SecurityGroup(self, "AlbSecurityGroup", vpc=vpc, allow_all_outbound=True)
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "Public HTTP")

        task_sg = ec2.SecurityGroup(self, "TaskSecurityGroup", vpc=vpc, allow_all_outbound=True)
        # Only ALB can reach the service on 8080
        task_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8080), "ALB to config-service")
        # Allow intra-task communication on etcd client/peer ports (same SG -> same SG)
        for p in [2379, 2380, 32379, 32380, 42379, 42380]:
            task_sg.add_ingress_rule(task_sg, ec2.Port.tcp(p), f"intra-task etcd port {p}")

        # Log group
        # Log group: use a fixed name only if provided; otherwise let CDK generate
        if log_group_name and str(log_group_name).lower() != "auto":
            log_group = logs.LogGroup(
                self,
                "ContainerLogs",
                log_group_name=log_group_name,
                retention=logs.RetentionDays.ONE_WEEK,
            )
        else:
            log_group = logs.LogGroup(
                self,
                "ContainerLogs",
                retention=logs.RetentionDays.ONE_WEEK,
            )

        # ECS Cluster
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)

        # Choose CPU architecture explicitly to avoid image/host mismatches
        cpu_arch = (
            ecs.CpuArchitecture.ARM64 if arch_ctx in ["arm64", "arm"] else ecs.CpuArchitecture.X86_64
        )

        # Task Definition (Fargate)
        task_def = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=cpu,
            memory_limit_mib=memory_mib,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=cpu_arch,
            ),
        )

        # Ephemeral volume for etcd data (non-persistent)
        task_def.add_volume(name="etcd-data")

        log_driver = ecs.LogDrivers.aws_logs(stream_prefix="config", log_group=log_group)

        # etcd containers
        def etcd_container(name: str, client_port: int, peer_port: int):
            env = {
                "ETCD_NAME": name,
                # Use a unique data dir per member to avoid corruption
                "ETCD_DATA_DIR": f"/etcd-data/{name}",
                "ETCD_LISTEN_CLIENT_URLS": f"http://0.0.0.0:{client_port}",
                "ETCD_LISTEN_PEER_URLS": f"http://0.0.0.0:{peer_port}",
                "ETCD_INITIAL_CLUSTER_TOKEN": "etcd-cluster-1",
                "ETCD_INITIAL_CLUSTER_STATE": "new",
            }
            c = task_def.add_container(
                name,
                image=ecs.ContainerImage.from_registry("quay.io/coreos/etcd:v3.5.3"),
                logging=log_driver,
                environment=env,
                essential=True,
                health_check=ecs.HealthCheck(
                    command=[
                        "CMD-SHELL",
                        f"etcdctl endpoint health --endpoints=http://127.0.0.1:{client_port} || exit 1",
                    ],
                    interval=Duration.seconds(15),
                    timeout=Duration.seconds(5),
                    retries=3,
                    start_period=Duration.seconds(10),
                ),
                command=[
                    "sh",
                    "-lc",
                    (
                        "IP=$(hostname -I | awk '{print $1}'); "
                        f"export ETCD_ADVERTISE_CLIENT_URLS=http://$IP:{client_port}; "
                        f"export ETCD_INITIAL_ADVERTISE_PEER_URLS=http://$IP:{peer_port}; "
                        "export ETCD_INITIAL_CLUSTER="
                        "etcd1=http://$IP:2380,etcd2=http://$IP:32380,etcd3=http://$IP:42380; "
                        "exec /usr/local/bin/etcd"
                    ),
                ],
            )
            c.add_port_mappings(ecs.PortMapping(container_port=client_port))
            c.add_port_mappings(ecs.PortMapping(container_port=peer_port))
            c.add_mount_points(
                ecs.MountPoint(container_path="/etcd-data", read_only=False, source_volume="etcd-data")
            )
            return c

        etcd1 = etcd_container("etcd1", client_port=2379, peer_port=2380)
        etcd2 = etcd_container("etcd2", client_port=32379, peer_port=32380)
        etcd3 = etcd_container("etcd3", client_port=42379, peer_port=42380)

        # config-service container
        if image_mode == "asset":
            # Build from repository root Dockerfile on deploy
            image = ecs.ContainerImage.from_asset(
                "../../",
                file="Dockerfile",
                # Force a specific platform so local ARM builds (e.g. M1/M2) work on x86 Fargate
                platform=(
                    ecr_assets.Platform.LINUX_ARM64
                    if cpu_arch == ecs.CpuArchitecture.ARM64
                    else ecr_assets.Platform.LINUX_AMD64
                ),
                exclude=[
                    ".git",
                    ".github",
                    ".venv",
                    ".test-venv",
                    "**/__pycache__/",
                    "**/.pytest_cache",
                    "**/.mypy_cache",
                    "cdk.out",
                    "**/cdk.out",
                    "infra/cdk/cdk.out",
                    "node_modules",
                    "**/node_modules",
                ],
            )
        else:
            repo = ecr.Repository.from_repository_name(self, "ConfigServiceRepo", ecr_repo_name)
            image = ecs.ContainerImage.from_ecr_repository(repository=repo, tag=ecr_tag)

        cfg_env = {
            "HOST": "0.0.0.0",
            "PORT": "8080",
        }

        config_container = task_def.add_container(
            "config-service",
            image=image,
            logging=log_driver,
            environment=cfg_env,
            essential=True,
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -fsS http://127.0.0.1:8080/health || exit 1"],
                interval=Duration.seconds(15),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
            command=[
                "sh",
                "-lc",
                (
                    "IP=$(hostname -I | awk '{print $1}'); "
                    "export ETCD_ENDPOINTS=$IP:2379,$IP:32379,$IP:42379; "
                    "exec /opt/venv/bin/python -m config_service.server"
                ),
            ],
        )
        config_container.add_port_mappings(ecs.PortMapping(container_port=8080))
        # Make config-service the default container for LB attachments
        task_def.default_container = config_container

        # ALB
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
        )
        listener = alb.add_listener("HttpListener", port=80, open=True)

        # Fargate Service (in public subnets; assign public IP for egress)
        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            security_groups=[task_sg],
            assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            health_check_grace_period=Duration.seconds(60),
        )

        # Target group and load balancer wiring
        tg = elbv2.ApplicationTargetGroup(
            self,
            "TargetGroup",
            vpc=vpc,
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(path="/health", healthy_http_codes="200"),
        )
        listener.add_target_groups("EcsTg", target_groups=[tg])

        # Attach service to the target group; it will use the default container/port
        service.attach_to_application_target_group(tg)

        # CloudFront Distribution (HTTPS front door with nicer URL)
        cf_origin = origins.LoadBalancerV2Origin(
            alb,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            keepalive_timeout=Duration.seconds(30),
            read_timeout=Duration.seconds(30),
        )

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=cf_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
            ),
            additional_behaviors={
                "/v1/*": cloudfront.BehaviorOptions(
                    origin=cf_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                ),
                "/cluster/*": cloudfront.BehaviorOptions(
                    origin=cf_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                ),
            },
        )

        # Ensure config-service starts after etcd containers are healthy
        config_container.add_container_dependencies(
            ecs.ContainerDependency(container=etcd1, condition=ecs.ContainerDependencyCondition.HEALTHY),
            ecs.ContainerDependency(container=etcd2, condition=ecs.ContainerDependencyCondition.HEALTHY),
            ecs.ContainerDependency(container=etcd3, condition=ecs.ContainerDependencyCondition.HEALTHY),
        )

        CfnOutput(self, "AlbDnsName", value=alb.load_balancer_dns_name)
        CfnOutput(self, "CloudFrontUrl", value=f"https://{distribution.domain_name}")
