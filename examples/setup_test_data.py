#!/usr/bin/env python3
"""
Script to set up test configuration data in etcd for demonstration.
"""

import json
import etcd3
from datetime import datetime


def main():
    print("🔧 Setting up test configuration data...")
    
    # Connect to etcd
    client = etcd3.client(host='etcd', port=2379)
    
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