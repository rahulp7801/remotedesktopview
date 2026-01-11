#!/usr/bin/env python3
"""
Test script for VAPI webhook endpoint.

Tests the webhook with mock payloads for different event types:
- tool-calls (execute_desktop_command)
- assistant-request
- status-update
- end-of-call-report

Usage:
    python test_webhook.py [--base-url http://localhost:8000]
"""

import asyncio
import json
import sys
import argparse
from datetime import datetime

import httpx


# Default configuration
DEFAULT_BASE_URL = "http://localhost:8000"
WEBHOOK_ENDPOINT = "/vapi/webhook"


# Mock payloads for different event types
MOCK_PAYLOADS = {
    "tool-calls": {
        "message": {
            "type": "tool-calls",
            "toolCallList": [
                {
                    "id": "call_001",
                    "name": "execute_desktop_command",
                    "arguments": {
                        "prompt": "Open Google Chrome",
                        "screenshot_after": True
                    }
                }
            ],
            "call": {
                "id": "call_session_123",
                "orgId": "org_456",
                "createdAt": datetime.now().isoformat(),
                "status": "in-progress"
            }
        }
    },
    "assistant-request": {
        "message": {
            "type": "assistant-request",
            "call": {
                "id": "call_session_123",
                "orgId": "org_456",
                "createdAt": datetime.now().isoformat(),
                "status": "ringing"
            }
        }
    },
    "status-update": {
        "message": {
            "type": "status-update",
            "status": "in-progress",
            "call": {
                "id": "call_session_123",
                "orgId": "org_456",
                "createdAt": datetime.now().isoformat(),
                "status": "in-progress"
            }
        }
    },
    "end-of-call-report": {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "call": {
                "id": "call_session_123",
                "orgId": "org_456",
                "createdAt": datetime.now().isoformat(),
                "status": "ended"
            },
            "summary": "User requested to open Chrome browser.",
            "transcript": "User: Open Chrome please. Assistant: Opening Google Chrome for you.",
            "recordingUrl": None,
            "messages": [
                {"role": "user", "message": "Open Chrome please"},
                {"role": "assistant", "message": "Opening Google Chrome for you."}
            ]
        }
    }
}


def print_header(title: str):
    """Print a formatted section header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def print_json(data: dict, indent: int = 2):
    """Print formatted JSON."""
    print(json.dumps(data, indent=indent, default=str))


def validate_tool_calls_response(response: dict) -> bool:
    """Validate the response structure for tool-calls event."""
    if "results" not in response:
        print("  [FAIL] Missing 'results' key in response")
        return False

    results = response["results"]
    if not isinstance(results, list):
        print("  [FAIL] 'results' should be a list")
        return False

    if len(results) == 0:
        print("  [WARN] 'results' list is empty")
        return True

    for i, result in enumerate(results):
        if "toolCallId" not in result:
            print(f"  [FAIL] Result {i} missing 'toolCallId'")
            return False
        if "result" not in result:
            print(f"  [FAIL] Result {i} missing 'result'")
            return False

    print("  [PASS] Response structure is valid")
    return True


def validate_assistant_request_response(response: dict) -> bool:
    """Validate the response structure for assistant-request event."""
    # Assistant request can return assistant config or empty
    if response == {}:
        print("  [PASS] Empty response (using default assistant)")
        return True

    if "assistant" in response:
        print("  [PASS] Response contains assistant configuration")
        return True

    print("  [WARN] Unexpected response structure")
    return True


def validate_status_update_response(response: dict) -> bool:
    """Validate the response structure for status-update event."""
    # Status update typically returns acknowledgment or empty
    print("  [PASS] Status update acknowledged")
    return True


def validate_end_of_call_response(response: dict) -> bool:
    """Validate the response structure for end-of-call-report event."""
    # End of call report typically returns acknowledgment or empty
    print("  [PASS] End of call report acknowledged")
    return True


VALIDATORS = {
    "tool-calls": validate_tool_calls_response,
    "assistant-request": validate_assistant_request_response,
    "status-update": validate_status_update_response,
    "end-of-call-report": validate_end_of_call_response
}


async def test_webhook(base_url: str, event_type: str, payload: dict) -> dict:
    """Send a test request to the webhook endpoint."""
    url = f"{base_url}{WEBHOOK_ENDPOINT}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"}
        )

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": response.json() if response.text else {}
        }


async def run_test(base_url: str, event_type: str) -> bool:
    """Run a single test case."""
    print_header(f"Testing: {event_type}")

    payload = MOCK_PAYLOADS[event_type]

    print("\nRequest Payload:")
    print_json(payload)

    try:
        result = await test_webhook(base_url, event_type, payload)

        print(f"\nResponse Status: {result['status_code']}")
        print("\nResponse Body:")
        print_json(result["body"])

        # Validate response
        print("\nValidation:")
        if result["status_code"] == 200:
            validator = VALIDATORS.get(event_type, lambda x: True)
            is_valid = validator(result["body"])
            return is_valid
        else:
            print(f"  [FAIL] Expected status 200, got {result['status_code']}")
            return False

    except httpx.ConnectError:
        print(f"\n  [ERROR] Could not connect to {base_url}")
        print("  Make sure the server is running: uvicorn main:app --reload")
        return False
    except Exception as e:
        print(f"\n  [ERROR] {type(e).__name__}: {e}")
        return False


async def run_all_tests(base_url: str):
    """Run all test cases."""
    print_header("VAPI Webhook Test Suite")
    print(f"Target: {base_url}{WEBHOOK_ENDPOINT}")
    print(f"Time: {datetime.now().isoformat()}")

    results = {}

    for event_type in MOCK_PAYLOADS.keys():
        results[event_type] = await run_test(base_url, event_type)

    # Summary
    print_header("Test Summary")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for event_type, success in results.items():
        status = "[PASS]" if success else "[FAIL]"
        print(f"  {status} {event_type}")

    print(f"\nTotal: {passed}/{total} tests passed")

    return all(results.values())


async def run_single_test(base_url: str, event_type: str):
    """Run a single test case by event type."""
    if event_type not in MOCK_PAYLOADS:
        print(f"Unknown event type: {event_type}")
        print(f"Available types: {', '.join(MOCK_PAYLOADS.keys())}")
        return False

    return await run_test(base_url, event_type)


def main():
    parser = argparse.ArgumentParser(description="Test VAPI webhook endpoint")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the server (default: {DEFAULT_BASE_URL})"
    )
    parser.add_argument(
        "--event",
        choices=list(MOCK_PAYLOADS.keys()),
        help="Test a specific event type only"
    )

    args = parser.parse_args()

    if args.event:
        success = asyncio.run(run_single_test(args.base_url, args.event))
    else:
        success = asyncio.run(run_all_tests(args.base_url))

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
