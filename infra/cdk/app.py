#!/usr/bin/env python3
import os
from aws_cdk import App, Environment
from stacks.ecs_fargate_stack import EcsFargateStack


app = App()

# Allow region/account from environment; default to CDK env
account = os.getenv("CDK_DEFAULT_ACCOUNT")
region = os.getenv("CDK_DEFAULT_REGION")

EcsFargateStack(
    app,
    "ConfigManagerEcsFargate",
    env=Environment(account=account, region=region),
)

app.synth()

