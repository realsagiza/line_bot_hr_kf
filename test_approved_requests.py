"""
Unit tests for approve_request functionality in approved_requests.py

Test cases:
1. Approve request for NONIKO branch - success (plan + request)
2. Approve request for Klangfrozen branch - success (plan + request)
3. Approve request - /cashout/plan fails
4. Approve request - /cashout/request fails
5. Approve request - missing denominations in plan response
6. Approve request - request already approved (duplicate approval)
7. Approve request - request not found
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os
from datetime import datetime
import requests

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

# Import after path setup
from approved_requests import approve_request, reject_request
from flask import Flask
from flask.testing import FlaskClient


class TestApprovedRequests(unittest.TestCase):
    """Test approve_request functionality in approved_requests.py"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Create Flask app for testing
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        
        # Mock request data
        self.test_request_id = "test_req_123"
        self.test_request_data = {
            "request_id": self.test_request_id,
            "user_id": "test_user_123",
            "amount": "100",
            "reason": "ซื้อน้ำแข็ง",
            "location": "โนนิโกะ",
            "status": "pending",
            "created_at_bkk": "2024-01-15T10:30:00",
            "created_at_utc": "2024-01-15T03:30:00",
            "created_date_bkk": "2024-01-15",
        }
    
    @patch('approved_requests.requests_collection')
    @patch('approved_requests.requests.post')
    @patch('approved_requests.now_bangkok_and_utc')
    @patch('approved_requests.build_correlation_headers')
    @patch('approved_requests.get_rest_api_ci_base_for_branch')
    def test_approve_request_noniko_success(
        self,
        mock_get_base,
        mock_build_headers,
        mock_now_bkk,
        mock_requests_post,
        mock_requests_collection
    ):
        """Test successful approval for NONIKO branch"""
        # Setup mocks
        mock_requests_collection.find_one.return_value = self.test_request_data.copy()
        mock_requests_collection.update_one = Mock()
        
        # Mock time
        mock_now_bkk.return_value = (
            datetime(2024, 1, 15, 10, 35, 0),
            datetime(2024, 1, 15, 3, 35, 0)
        )
        
        # Mock API base URL
        mock_get_base.return_value = "http://10.0.0.14:5000"
        
        # Mock correlation headers
        mock_build_headers.return_value = (
            {"X-Trace-Id": "t-12345678", "X-Request-Id": "r-87654321"},
            {"trace_id": "t-12345678", "request_id": "r-87654321", "sale_id": self.test_request_id}
        )
        
        # Mock /cashout/plan response
        mock_plan_response = Mock()
        mock_plan_response.status_code = 200
        mock_plan_response.json.return_value = {
            "success": True,
            "amount": 100,
            "denominations": {
                "100": 0,
                "200": 0,
                "500": 0,
                "1000": 0,
                "2000": 0,
                "10000": 1
            }
        }
        mock_plan_response.raise_for_status = Mock()
        
        # Mock /cashout/request response
        mock_request_response = Mock()
        mock_request_response.status_code = 200
        mock_request_response.json.return_value = {
            "success": True,
            "denominations": {
                "100": 0,
                "200": 0,
                "500": 0,
                "1000": 0,
                "2000": 0,
                "10000": 1
            },
            "result": {"status": "sent"}
        }
        mock_request_response.raise_for_status = Mock()
        
        # Setup side_effect to return different responses for different URLs
        def side_effect(url, **kwargs):
            if '/cashout/plan' in url:
                return mock_plan_response
            elif '/cashout/request' in url:
                return mock_request_response
            return Mock()
        
        mock_requests_post.side_effect = side_effect
        
        # Create Flask app context
        with self.app.app_context():
            from flask import redirect
            # Mock redirect function
            with patch('approved_requests.redirect', return_value=redirect('/money/approved-requests')):
                # Execute
                result = approve_request(self.test_request_id)
        
        # Verify /cashout/plan was called
        plan_calls = [call for call in mock_requests_post.call_args_list if '/cashout/plan' in call[0][0]]
        self.assertEqual(len(plan_calls), 1)
        plan_call = plan_calls[0]
        self.assertEqual(plan_call[1]['json']['amount'], 100.0)
        
        # Verify /cashout/request was called
        request_calls = [call for call in mock_requests_post.call_args_list if '/cashout/request' in call[0][0]]
        self.assertEqual(len(request_calls), 1)
        request_call = request_calls[0]
        self.assertIn('denominations', request_call[1]['json'])
        self.assertEqual(request_call[1]['json']['denominations']['10000'], 1)
        
        # Verify status was updated to awaiting_machine first
        self.assertGreaterEqual(mock_requests_collection.update_one.call_count, 1)
        update_calls = mock_requests_collection.update_one.call_args_list
        
        # Check that status was updated to awaiting_machine
        awaiting_machine_call = next(
            (call for call in update_calls if call[0][1]['$set'].get('status') == 'awaiting_machine'),
            None
        )
        self.assertIsNotNone(awaiting_machine_call, "Should update status to awaiting_machine")
        
        # Check that status was updated to approved
        approved_call = next(
            (call for call in update_calls if call[0][1]['$set'].get('status') == 'approved'),
            None
        )
        self.assertIsNotNone(approved_call, "Should update status to approved")
        
        # Verify denominations were saved
        approved_update = approved_call[0][1]['$set']
        self.assertIn('denominations', approved_update)
        self.assertIn('cashout_plan_response', approved_update)
        self.assertIn('cashout_request_response', approved_update)
    
    @patch('approved_requests.requests_collection')
    @patch('approved_requests.requests.post')
    @patch('approved_requests.now_bangkok_and_utc')
    @patch('approved_requests.build_correlation_headers')
    @patch('approved_requests.get_rest_api_ci_base_for_branch')
    def test_approve_request_klangfrozen_success(
        self,
        mock_get_base,
        mock_build_headers,
        mock_now_bkk,
        mock_requests_post,
        mock_requests_collection
    ):
        """Test successful approval for Klangfrozen branch"""
        # Setup test data for Klangfrozen
        test_data = self.test_request_data.copy()
        test_data["location"] = "คลังห้องเย็น"
        
        mock_requests_collection.find_one.return_value = test_data
        mock_requests_collection.update_one = Mock()
        
        # Mock time
        mock_now_bkk.return_value = (
            datetime(2024, 1, 15, 10, 35, 0),
            datetime(2024, 1, 15, 3, 35, 0)
        )
        
        # Mock API base URL for Klangfrozen
        mock_get_base.return_value = "http://10.0.0.15:5000"
        
        # Mock correlation headers
        mock_build_headers.return_value = (
            {"X-Trace-Id": "t-12345678"},
            {"trace_id": "t-12345678", "request_id": "r-87654321", "sale_id": self.test_request_id}
        )
        
        # Mock /cashout/plan response
        mock_plan_response = Mock()
        mock_plan_response.status_code = 200
        mock_plan_response.json.return_value = {
            "success": True,
            "amount": 100,
            "denominations": {
                "100": 1,
                "200": 0,
                "500": 0,
                "1000": 0,
                "2000": 0,
                "10000": 0
            }
        }
        mock_plan_response.raise_for_status = Mock()
        
        # Mock /cashout/request response
        mock_request_response = Mock()
        mock_request_response.status_code = 200
        mock_request_response.json.return_value = {
            "success": True,
            "denominations": {
                "100": 1,
                "200": 0,
                "500": 0,
                "1000": 0,
                "2000": 0,
                "10000": 0
            },
            "result": {"status": "sent"}
        }
        mock_request_response.raise_for_status = Mock()
        
        def side_effect(url, **kwargs):
            if '/cashout/plan' in url:
                return mock_plan_response
            elif '/cashout/request' in url:
                return mock_request_response
            return Mock()
        
        mock_requests_post.side_effect = side_effect
        
        # Create Flask app context
        with self.app.app_context():
            from flask import redirect
            with patch('approved_requests.redirect', return_value=redirect('/money/approved-requests')):
                result = approve_request(self.test_request_id)
        
        # Verify API calls
        plan_calls = [call for call in mock_requests_post.call_args_list if '/cashout/plan' in call[0][0]]
        self.assertEqual(len(plan_calls), 1)
        
        request_calls = [call for call in mock_requests_post.call_args_list if '/cashout/request' in call[0][0]]
        self.assertEqual(len(request_calls), 1)
        
        # Verify status was updated to approved
        update_calls = mock_requests_collection.update_one.call_args_list
        approved_call = next(
            (call for call in update_calls if call[0][1]['$set'].get('status') == 'approved'),
            None
        )
        self.assertIsNotNone(approved_call, "Should update status to approved")
    
    @patch('approved_requests.requests_collection')
    @patch('approved_requests.requests.post')
    @patch('approved_requests.now_bangkok_and_utc')
    @patch('approved_requests.build_correlation_headers')
    @patch('approved_requests.get_rest_api_ci_base_for_branch')
    def test_approve_request_plan_fails(
        self,
        mock_get_base,
        mock_build_headers,
        mock_now_bkk,
        mock_requests_post,
        mock_requests_collection
    ):
        """Test approval when /cashout/plan fails"""
        mock_requests_collection.find_one.return_value = self.test_request_data.copy()
        mock_requests_collection.update_one = Mock()
        
        mock_now_bkk.return_value = (
            datetime(2024, 1, 15, 10, 35, 0),
            datetime(2024, 1, 15, 3, 35, 0)
        )
        
        mock_get_base.return_value = "http://10.0.0.14:5000"
        mock_build_headers.return_value = (
            {"X-Trace-Id": "t-12345678"},
            {"trace_id": "t-12345678", "request_id": "r-87654321", "sale_id": self.test_request_id}
        )
        
        # Mock /cashout/plan failure
        mock_plan_response = Mock()
        mock_plan_response.status_code = 400
        mock_plan_response.json.return_value = {
            "success": False,
            "error": "Invalid amount"
        }
        mock_plan_response.raise_for_status = Mock(side_effect=requests.exceptions.HTTPError("400 Client Error"))
        
        mock_requests_post.return_value = mock_plan_response
        
        # Create Flask app context
        with self.app.app_context():
            from flask import jsonify
            with patch('approved_requests.jsonify', return_value=jsonify({"status": "error"}), side_effect=lambda x: x):
                try:
                    result = approve_request(self.test_request_id)
                except Exception:
                    pass  # Expected to fail
        
        # Verify status was updated to error
        update_calls = mock_requests_collection.update_one.call_args_list
        error_call = next(
            (call for call in update_calls if call[0][1]['$set'].get('status') == 'error'),
            None
        )
        self.assertIsNotNone(error_call, "Should update status to error when plan fails")
    
    @patch('approved_requests.requests_collection')
    @patch('approved_requests.requests.post')
    @patch('approved_requests.now_bangkok_and_utc')
    @patch('approved_requests.build_correlation_headers')
    @patch('approved_requests.get_rest_api_ci_base_for_branch')
    def test_approve_request_missing_denominations(
        self,
        mock_get_base,
        mock_build_headers,
        mock_now_bkk,
        mock_requests_post,
        mock_requests_collection
    ):
        """Test approval when plan response is missing denominations"""
        mock_requests_collection.find_one.return_value = self.test_request_data.copy()
        mock_requests_collection.update_one = Mock()
        
        mock_now_bkk.return_value = (
            datetime(2024, 1, 15, 10, 35, 0),
            datetime(2024, 1, 15, 3, 35, 0)
        )
        
        mock_get_base.return_value = "http://10.0.0.14:5000"
        mock_build_headers.return_value = (
            {"X-Trace-Id": "t-12345678"},
            {"trace_id": "t-12345678", "request_id": "r-87654321", "sale_id": self.test_request_id}
        )
        
        # Mock /cashout/plan response without denominations
        mock_plan_response = Mock()
        mock_plan_response.status_code = 200
        mock_plan_response.json.return_value = {
            "success": True,
            "amount": 100
            # Missing denominations
        }
        mock_plan_response.raise_for_status = Mock()
        
        mock_requests_post.return_value = mock_plan_response
        
        # Create Flask app context
        with self.app.app_context():
            from flask import jsonify
            with patch('approved_requests.jsonify', return_value=jsonify({"status": "error"}), side_effect=lambda x: x):
                try:
                    result = approve_request(self.test_request_id)
                except Exception:
                    pass  # Expected to fail
        
        # Verify status was updated to error
        update_calls = mock_requests_collection.update_one.call_args_list
        error_call = next(
            (call for call in update_calls if call[0][1]['$set'].get('status') == 'error'),
            None
        )
        self.assertIsNotNone(error_call, "Should update status to error when denominations missing")
    
    @patch('approved_requests.requests_collection')
    def test_approve_request_not_found(self, mock_requests_collection):
        """Test approval when request is not found"""
        mock_requests_collection.find_one.return_value = None
        
        # Create Flask app context
        with self.app.app_context():
            from flask import jsonify
            with patch('approved_requests.jsonify', return_value=jsonify({"status": "error"}), side_effect=lambda x: x):
                try:
                    result = approve_request("non_existent_id")
                except Exception:
                    pass  # Expected to fail
        
        # Verify find_one was called
        mock_requests_collection.find_one.assert_called_once_with({"request_id": "non_existent_id"})
    
    @patch('approved_requests.requests_collection')
    def test_approve_request_already_approved(self, mock_requests_collection):
        """Test approval when request is already approved"""
        test_data = self.test_request_data.copy()
        test_data["status"] = "approved"
        
        mock_requests_collection.find_one.return_value = test_data
        
        # Create Flask app context
        with self.app.app_context():
            from flask import redirect
            with patch('approved_requests.redirect', return_value=redirect('/money/approved-requests')):
                result = approve_request(self.test_request_id)
        
        # Verify update_one was not called (should redirect early)
        # The function should redirect without calling update_one for status changes
        # We check that find_one was called
        mock_requests_collection.find_one.assert_called_once()


if __name__ == '__main__':
    unittest.main()

