#!/usr/bin/env python3
"""
Script to set up test configuration data in etcd for demonstration.
"""

import json
import os
import etcd3
from datetime import datetime


def connect_to_etcd() -> etcd3.Etcd3Client:
    """Connect to the first healthy etcd endpoint.

    Endpoints are read from ETCD_ENDPOINTS (comma-separated host:port).
    Defaults to a 3-node local cluster with a localhost fallback so the
    script works both inside the Compose network and on the host.
    """
    endpoints_env = os.getenv(
        "ETCD_ENDPOINTS",
        # Compose service names (inside network) + host ports (outside)
        "etcd1:2379,etcd2:2379,etcd3:2379,localhost:2379,localhost:22379,localhost:32379",
    )
    endpoints = [e.strip() for e in endpoints_env.split(",") if e.strip()]

    last_err = None
    for ep in endpoints:
        try:
            host, port_str = ep.split(":", 1)
            client = etcd3.client(host=host, port=int(port_str))
            # Validate connection
            client.status()
            print(f"✅ Connected to etcd at {ep}")
            return client
        except Exception as e:
            print(f"⚠️  Failed to connect to {ep}: {e}")
            last_err = e

    raise RuntimeError(f"Unable to connect to any etcd endpoint: {endpoints}") from last_err


def main():
    print("🔧 Setting up test configuration data...")
    
    # Connect to etcd (tries ETCD_ENDPOINTS or sensible defaults)
    client = connect_to_etcd()
    
    # Test data for example-app
    test_configs = {
        "/config/example-app/development/debug": {
            "value": True,
            "version": "v1.0.0",
            "metadata": {
                "created_by": "setup-script",
                "created_at": datetime.now().isoformat(),
                "approved_by": "admin@example.com",
                "git_commit": "initial"
            }
        },
        "/config/example-app/development/database.pool_size": {
            "value": 20,
            "version": "v1.0.0",
            "metadata": {
                "created_by": "setup-script",
                "created_at": datetime.now().isoformat(),
                "approved_by": "admin@example.com",
                "git_commit": "initial"
            }
        },
        "/config/example-app/development/database.host": {
            "value": "localhost",
            "version": "v1.0.0",
            "metadata": {
                "created_by": "setup-script",
                "created_at": datetime.now().isoformat(),
                "approved_by": "admin@example.com",
                "git_commit": "initial"
            }
        },
        "/config/example-app/development/feature_flags.new_ui": {
            "value": True,
            "version": "v1.0.0",
            "metadata": {
                "created_by": "setup-script",
                "created_at": datetime.now().isoformat(),
                "approved_by": "admin@example.com",
                "git_commit": "initial"
            }
        },
        "/config/example-app/development/api.timeout": {
            "value": 30,
            "version": "v1.0.0",
            "metadata": {
                "created_by": "setup-script",
                "created_at": datetime.now().isoformat(),
                "approved_by": "admin@example.com",
                "git_commit": "initial"
            }
        }
    }
    
    # Insert test data
    for key, config_data in test_configs.items():
        json_value = json.dumps(config_data)
        client.put(key, json_value)
        print(f"✅ Set {key} = {config_data['value']}")
    
    print(f"\n🎉 Successfully set up {len(test_configs)} test configurations!")
    print("\nYou can now run the example application to see the configs in action.")


if __name__ == "__main__":
    main()
