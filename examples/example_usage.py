#!/usr/bin/env python3
"""
Example application demonstrating config service integration.

This shows how easy it is to integrate the config service into any Python application.
"""

import time
import sys
import os

# Add the config_client to the path (in real usage, this would be pip installed)
sys.path.insert(0, '/app')

from config_client import ConfigClient


def main():
    print("🚀 Config Service Example Application")
    print("=" * 50)
    
    # Initialize the config client
    config = ConfigClient()
    
    print(f"📡 Connected to config service at: {config.base_url}")
    print(f"📋 Service: {config.namespace}")
    print(f"🌍 Environment: {config.environment}")
    print()
    
    # Example 1: Get a specific config with default
    print("Example 1: Get config with default value")
    debug_mode = config.get("debug", default=False)
    print(f"debug = {debug_mode}")
    print()
    
    # Example 2: Get an integer config with validation
    print("Example 2: Get integer config with validation")
    try:
        pool_size = config.get_int("database.pool_size", default=10, min_val=1, max_val=100)
        print(f"database.pool_size = {pool_size}")
    except Exception as e:
        print(f"Error: {e}")
    print()
    
    # Example 3: Get a boolean config
    print("Example 3: Get boolean config")
    feature_enabled = config.get_bool("feature_flags.new_ui", default=False)
    print(f"feature_flags.new_ui = {feature_enabled}")
    print()
    
    # Example 4: Get all configs
    print("Example 4: Get all configurations")
    all_configs = config.get_all()
    if all_configs:
        print("All configurations:")
        for key, value in all_configs.items():
            print(f"  {key} = {value}")
    else:
        print("No configurations found (this is expected for a fresh setup)")
    print()
    
    # Example 5: Demonstrate caching
    print("Example 5: Demonstrate caching behavior")
    print("First request (will hit config service):")
    start_time = time.time()
    value1 = config.get("test.cache", default="cached_value")
    time1 = time.time() - start_time
    print(f"  test.cache = {value1} (took {time1:.3f}s)")
    
    print("Second request (will use cache):")
    start_time = time.time()
    value2 = config.get("test.cache", default="cached_value")
    time2 = time.time() - start_time
    print(f"  test.cache = {value2} (took {time2:.3f}s)")
    print()
    
    # Example 6: Demonstrate graceful degradation
    print("Example 6: Graceful degradation")
    print("Trying to get a non-existent config:")
    missing_value = config.get("non.existent.key", default="fallback_value")
    print(f"  non.existent.key = {missing_value}")
    print()
    
    print("✅ Config service integration working successfully!")
    print()
    print("Next steps:")
    print("1. Add some configuration to etcd using the management API")
    print("2. See how your app automatically picks up the new values")
    print("3. Test real-time updates (coming in Milestone 2)")


if __name__ == "__main__":
    main()