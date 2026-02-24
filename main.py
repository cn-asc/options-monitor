#!/usr/bin/env python3
"""
Cloud Function entry point for Options Dashboard v2
This wraps app_v2.py's main() function for Google Cloud Functions
"""
import os
import sys
import json

# Add current directory to path so we can import local modules
sys.path.insert(0, os.path.dirname(__file__))

# Import the main function from app_v2
from app_v2 import main

def options_dashboard_cloud_function(request):
    """
    Cloud Function entry point.
    request: Flask Request object (unused, but required by Cloud Functions)
    """
    try:
        # Run the main dashboard function
        main()
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Options dashboard executed successfully',
                'timestamp': str(os.environ.get('X_TIMESTAMP', 'unknown'))
            })
        }
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error: {error_msg}", file=sys.stderr)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': error_msg,
                'timestamp': str(os.environ.get('X_TIMESTAMP', 'unknown'))
            })
        }
