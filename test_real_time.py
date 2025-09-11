#!/usr/bin/env python3
"""
Test script to verify that config changes propagate within 1-2 seconds.
This tests the real-time update functionality implemented in Milestone 2.
"""

import asyncio
import time
import json
import httpx
from config_client.client import ConfigClient


async def test_real_time_updates():
    """Test that configuration changes propagate quickly via sidecar cache"""
    
    print("🧪 Testing real-time configuration updates...")
    
    # Initialize simple client - no WebSocket complexity
    client = ConfigClient(
        namespace="test-service",
        environment="development"
    )
    
    # Give sidecar time to establish etcd watch
    print("⏳ Waiting for sidecar to establish etcd watch...")
    await asyncio.sleep(2)
    
    try:
        # Test 1: Emergency override endpoint
        print("\n📝 Test 1: Emergency override")
        start_time = time.time()
        
        # Make emergency override request
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                "http://localhost:8080/v1/emergency/test-service/development/test.key",
                json={
                    "value": "emergency_value_123",
                    "reason": "Testing real-time updates",
                    "created_by": "test-script"
                }
            )
            
            if response.status_code == 200:
                print(f"✅ Emergency override successful: {response.json()}")
            else:
                print(f"❌ Emergency override failed: {response.status_code} - {response.text}")
                return False
        
        # Wait for change to propagate to sidecar cache
        print("⏳ Waiting for change to propagate to sidecar cache...")
        max_wait = 3.0  # Max 3 seconds
        propagation_time = None
        
        for attempt in range(int(max_wait * 10)):  # Check every 100ms
            await asyncio.sleep(0.1)
            
            try:
                value = client.get("test.key")
                if value == "emergency_value_123":
                    propagation_time = time.time() - start_time
                    break
            except Exception:
                continue  # Keep trying
        
        if propagation_time is not None:
            print(f"⚡ Propagation time: {propagation_time:.3f} seconds")
            
            if propagation_time <= 2.0:
                print("✅ PASS: Config change propagated within 2 seconds")
            else:
                print("❌ FAIL: Config change took too long to propagate")
                return False
        else:
            print("❌ FAIL: Config change never propagated to sidecar cache")
            return False
        
        # Test 2: Verify client can read the new value
        print("\n📖 Test 2: Client read after change")
        value = client.get("test.key")
        
        if value == "emergency_value_123":
            print("✅ PASS: Client reads updated value correctly")
        else:
            print(f"❌ FAIL: Client read incorrect value: {value}")
            return False
        
        # Test 3: Deploy endpoint
        print("\n📦 Test 3: Deploy endpoint")
        start_time = time.time()
        
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                "http://localhost:8080/v1/deploy/test-service/development",
                json={
                    "git_ref": "v1.2.3",
                    "created_by": "test-script",
                    "configs": {
                        "database.host": "new-db-host.example.com",
                        "database.port": 5432,
                        "feature.enabled": True
                    }
                }
            )
            
            if response.status_code == 200:
                print(f"✅ Deploy successful: {response.json()}")
            else:
                print(f"❌ Deploy failed: {response.status_code} - {response.text}")
                return False
        
        # Wait for changes to propagate
        print("⏳ Waiting for deploy changes to propagate...")
        await asyncio.sleep(1)  # Give sidecar a moment to update
        
        # Check if all deployed configs are now available
        expected_configs = {
            "database.host": "new-db-host.example.com",
            "database.port": 5432,
            "feature.enabled": True
        }
        
        all_propagated = False
        for attempt in range(20):  # Check for 2 seconds
            try:
                all_configs = client.get_all()
                all_propagated = all(
                    all_configs.get(key) == expected for key, expected in expected_configs.items()
                )
                if all_propagated:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)
        
        propagation_time = time.time() - start_time
        print(f"⚡ Deploy propagation time: {propagation_time:.3f} seconds")
        
        if all_propagated and propagation_time <= 2.0:
            print("✅ PASS: All deploy changes propagated within 2 seconds")
        elif not all_propagated:
            print("❌ FAIL: Not all deployed configs propagated")
            return False
        else:
            print("❌ FAIL: Deploy changes took too long to propagate")
            return False
        
        # Test 4: Verify all values are readable
        print("\n📚 Test 4: Read all deployed values")
        all_configs = client.get_all()
        
        expected_values = {
            "database.host": "new-db-host.example.com",
            "database.port": 5432,
            "feature.enabled": True,
            "test.key": "emergency_value_123"
        }
        
        success = True
        for key, expected in expected_values.items():
            actual = all_configs.get(key)
            if actual == expected:
                print(f"✅ {key}: {actual}")
            else:
                print(f"❌ {key}: expected {expected}, got {actual}")
                success = False
        
        if success:
            print("✅ PASS: All values readable and correct")
        else:
            print("❌ FAIL: Some values incorrect")
            return False
        
        print("\n🎉 All tests passed! Milestone 2 implementation is working correctly.")
        return True
        
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        return False
    
    finally:
        # Clean up
        client.close()


async def main():
    """Main test runner"""
    print("🚀 Starting Config Service Real-time Update Tests")
    print("Make sure the config service is running on localhost:8080")
    print("And etcd is running and accessible")
    
    # Wait a moment for user to see the message
    await asyncio.sleep(1)
    
    success = await test_real_time_updates()
    
    if success:
        print("\n✅ ALL TESTS PASSED - Milestone 2 Complete!")
        return 0
    else:
        print("\n❌ SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    import sys
    exit_code = asyncio.run(main())
    sys.exit(exit_code)