"""Test script to verify Backblaze storage connection."""

import sys
from app.common.storage import get_s3_client
from app.common.config import get_settings

def test_b2_connection():
    """Test storage connection by listing bucket."""
    try:
        settings = get_settings()
        print(f"Testing storage connection...")
        print(f"Endpoint: {settings.storage_endpoint}")
        print(f"Bucket: {settings.storage_bucket}")
        print(f"Region: {settings.storage_region}")
        print()
        
        # Get S3 client
        s3_client = get_s3_client()
        
        # Test connection by listing objects (limit to 1)
        response = s3_client.list_objects_v2(
            Bucket=settings.storage_bucket,
            MaxKeys=1
        )
        
        print("✓ Successfully connected to Backblaze B2!")
        print(f"✓ Bucket '{settings.storage_bucket}' is accessible")
        
        if 'Contents' in response:
            print(f"✓ Bucket contains {response.get('KeyCount', 0)} objects (showing max 1)")
        else:
            print("✓ Bucket is empty")
        
        return True
        
    except ValueError as e:
        print(f"✗ Configuration error: {e}")
        return False
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print(f"Error type: {type(e).__name__}")
        return False

if __name__ == "__main__":
    success = test_b2_connection()
    sys.exit(0 if success else 1)


