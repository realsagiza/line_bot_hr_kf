"""
Unit tests for deposit functionality in handlers.py

Test cases:
1. Deposit to NONIKO branch - success
2. Deposit to NONIKO branch - API error
3. Deposit to Klangfrozen (cold_storage) branch - success
4. Deposit to Klangfrozen (cold_storage) branch - API error
5. MongoDB insertion for deposit requests
6. Transaction recording when deposit succeeds
7. Error handling and status updates
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, call
import sys
import os
from datetime import datetime

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

# Import after path setup
from handlers import handle_postback, reset_state, user_session
from linebot.models import PostbackEvent, SourceUser, TextSendMessage, TemplateSendMessage


class TestHandlersDeposit(unittest.TestCase):
    """Test deposit functionality in handlers.py"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Clear user_session before each test
        user_session.clear()
        
        # Mock line_bot_api
        self.mock_line_bot_api = Mock()
        
        # Create mock postback event
        self.mock_event = Mock(spec=PostbackEvent)
        self.mock_event.reply_token = "test_reply_token"
        self.mock_event.source = Mock(spec=SourceUser)
        self.mock_event.source.user_id = "test_user_123"
        self.mock_event.postback = Mock()
        
    def tearDown(self):
        """Clean up after each test"""
        user_session.clear()
    
    @patch('handlers.deposit_requests_collection')
    @patch('handlers.transactions_collection')
    @patch('handlers.requests')
    @patch('handlers.now_bangkok_and_utc')
    @patch('handlers.build_correlation_headers')
    @patch('handlers.get_rest_api_ci_base_for_branch')
    @patch('handlers.uuid')
    def test_deposit_noniko_success(
        self, 
        mock_uuid, 
        mock_get_base, 
        mock_build_headers,
        mock_now_bkk,
        mock_requests,
        mock_transactions_collection,
        mock_deposit_collection
    ):
        """Test successful deposit to NONIKO branch"""
        # Setup mocks
        user_id = "test_user_123"
        reset_state(user_id)
        user_session[user_id]["state"] = "waiting_for_location_deposit"
        user_session[user_id]["amount"] = "500"
        user_session[user_id]["reason"] = "change"
        user_session[user_id]["location"] = "noniko"
        
        # Mock UUID
        mock_uuid_obj = Mock()
        mock_uuid_obj.hex = "abcdef1234567890"
        mock_uuid.uuid4.return_value = mock_uuid_obj
        
        # Mock time
        mock_now_bkk.return_value = (
            datetime(2024, 1, 15, 10, 30, 0),
            datetime(2024, 1, 15, 3, 30, 0)
        )
        
        # Mock API base URL
        mock_get_base.return_value = "http://10.0.0.14:5000"
        
        # Mock correlation headers
        mock_build_headers.return_value = (
            {"X-Trace-Id": "t-12345678", "X-Request-Id": "r-87654321"},
            {"trace_id": "t-12345678", "request_id": "r-87654321", "sale_id": "d-abcdef12"}
        )
        
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status = Mock()
        mock_requests.post.return_value = mock_response
        
        # Mock MongoDB collections
        mock_deposit_collection.insert_one = Mock()
        mock_deposit_collection.update_one = Mock()
        mock_transactions_collection.insert_one = Mock()
        
        # Setup postback event
        self.mock_event.postback.data = f"select_location|noniko|{user_id}"
        
        # Execute
        handle_postback(self.mock_event, self.mock_line_bot_api)
        
        # Verify API was called correctly
        mock_requests.post.assert_called_once()
        call_args = mock_requests.post.call_args
        self.assertEqual(call_args[0][0], "http://10.0.0.14:5000/replenishment/start")
        self.assertEqual(call_args[1]["json"]["seq_no"], "1")
        self.assertEqual(call_args[1]["json"]["session_id"], "d-abcdef12")
        
        # Verify deposit request was inserted
        self.assertEqual(mock_deposit_collection.insert_one.call_count, 1)
        insert_call = mock_deposit_collection.insert_one.call_args[0][0]
        self.assertEqual(insert_call["amount"], 500)
        self.assertEqual(insert_call["location"], "โนนิโกะ")
        self.assertEqual(insert_call["reason"], "เงินทอน")
        self.assertEqual(insert_call["status"], "replenishment_started")
        self.assertEqual(insert_call["channel"], "line_bot")
        self.assertEqual(insert_call["deposit_request_id"], "d-abcdef12")
        self.assertEqual(insert_call["session_id"], "d-abcdef12")
        self.assertEqual(insert_call["seq_no"], "1")
        
        # No status update on success start (only insert initial doc)
        self.assertEqual(mock_deposit_collection.update_one.call_count, 0)
        
        # No transaction inserted at start step
        self.assertEqual(mock_transactions_collection.insert_one.call_count, 0)
        
        # Verify reply message was sent
        self.mock_line_bot_api.reply_message.assert_called_once()
        reply_args = self.mock_line_bot_api.reply_message.call_args
        self.assertIsInstance(reply_args[0][1], TemplateSendMessage)
        self.assertIn("เริ่มต้นการฝากเงิน", reply_args[0][1].template.text)
    
    @patch('handlers.deposit_requests_collection')
    @patch('handlers.transactions_collection')
    @patch('handlers.requests.post')
    @patch('handlers.now_bangkok_and_utc')
    @patch('handlers.build_correlation_headers')
    @patch('handlers.get_rest_api_ci_base_for_branch')
    @patch('handlers.uuid')
    def test_deposit_noniko_api_error(
        self,
        mock_uuid,
        mock_get_base,
        mock_build_headers,
        mock_now_bkk,
        mock_requests_post,
        mock_transactions_collection,
        mock_deposit_collection
    ):
        """Test deposit to NONIKO branch with API error"""
        # Setup mocks
        user_id = "test_user_123"
        reset_state(user_id)
        user_session[user_id]["state"] = "waiting_for_location_deposit"
        user_session[user_id]["amount"] = "300"
        user_session[user_id]["reason"] = "daily_sales"
        user_session[user_id]["location"] = "noniko"
        
        # Mock UUID
        mock_uuid_obj = Mock()
        mock_uuid_obj.hex = "abcdef1234567890"
        mock_uuid.uuid4.return_value = mock_uuid_obj
        
        # Mock time
        mock_now_bkk.return_value = (
            datetime(2024, 1, 15, 10, 30, 0),
            datetime(2024, 1, 15, 3, 30, 0)
        )
        
        # Mock API base URL
        mock_get_base.return_value = "http://10.0.0.14:5000"
        
        # Mock correlation headers
        mock_build_headers.return_value = (
            {"X-Trace-Id": "t-12345678"},
            {"trace_id": "t-12345678", "request_id": "r-87654321", "sale_id": "d-abcdef12"}
        )
        
        # Mock API error - use real requests exception
        import requests
        mock_requests_post.side_effect = requests.exceptions.RequestException("Connection error")
        
        # Mock MongoDB collections
        mock_deposit_collection.insert_one = Mock()
        mock_deposit_collection.update_one = Mock()
        
        # Setup postback event
        self.mock_event.postback.data = f"select_location|noniko|{user_id}"
        
        # Execute
        handle_postback(self.mock_event, self.mock_line_bot_api)
        
        # Verify deposit request was inserted
        self.assertEqual(mock_deposit_collection.insert_one.call_count, 1)
        
        # Verify status was updated to error
        self.assertEqual(mock_deposit_collection.update_one.call_count, 1)
        update_call = mock_deposit_collection.update_one.call_args
        self.assertEqual(update_call[0][1]["$set"]["status"], "error")
        self.assertIn("error_message", update_call[0][1]["$set"])
        
        # Verify transaction was NOT inserted (because API failed)
        self.assertEqual(mock_transactions_collection.insert_one.call_count, 0)
        
        # Verify error message was sent
        self.mock_line_bot_api.reply_message.assert_called_once()
        reply_args = self.mock_line_bot_api.reply_message.call_args
        self.assertIn("เกิดข้อผิดพลาด", reply_args[0][1].text)
    
    @patch('handlers.deposit_requests_collection')
    @patch('handlers.transactions_collection')
    @patch('handlers.requests.post')
    @patch('handlers.now_bangkok_and_utc')
    @patch('handlers.build_correlation_headers')
    @patch('handlers.get_rest_api_ci_base_for_branch')
    @patch('handlers.uuid')
    def test_deposit_cold_storage_success(
        self,
        mock_uuid,
        mock_get_base,
        mock_build_headers,
        mock_now_bkk,
        mock_requests_post,
        mock_transactions_collection,
        mock_deposit_collection
    ):
        """Test successful deposit to Klangfrozen (cold_storage) branch"""
        # Setup mocks
        user_id = "test_user_456"
        reset_state(user_id)
        user_session[user_id]["state"] = "waiting_for_location_deposit"
        user_session[user_id]["amount"] = "1000"
        user_session[user_id]["reason"] = "change"
        user_session[user_id]["location"] = "cold_storage"
        
        # Mock UUID
        mock_uuid_obj = Mock()
        mock_uuid_obj.hex = "fedcba0987654321"
        mock_uuid.uuid4.return_value = mock_uuid_obj
        
        # Mock time
        mock_now_bkk.return_value = (
            datetime(2024, 1, 15, 11, 0, 0),
            datetime(2024, 1, 15, 4, 0, 0)
        )
        
        # Mock API base URL
        mock_get_base.return_value = "http://10.0.0.15:5000"
        
        # Mock correlation headers
        mock_build_headers.return_value = (
            {"X-Trace-Id": "t-abcdef12", "X-Request-Id": "r-21fedcba"},
            {"trace_id": "t-abcdef12", "request_id": "r-21fedcba", "sale_id": "d-fedcba09"}
        )
        
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_response.raise_for_status = Mock()
        mock_requests_post.return_value = mock_response
        
        # Mock MongoDB collections
        mock_deposit_collection.insert_one = Mock()
        mock_deposit_collection.update_one = Mock()
        mock_transactions_collection.insert_one = Mock()
        
        # Setup postback event
        self.mock_event.postback.data = f"select_location|cold_storage|{user_id}"
        
        # Execute
        handle_postback(self.mock_event, self.mock_line_bot_api)
        
        # Verify API was called correctly
        mock_requests_post.assert_called_once()
        call_args = mock_requests_post.call_args
        self.assertEqual(call_args[0][0], "http://10.0.0.15:5000/replenishment/start")
        self.assertEqual(call_args[1]["json"]["seq_no"], "1")
        self.assertEqual(call_args[1]["json"]["session_id"], "d-fedcba09")
        
        # Verify deposit request was inserted
        self.assertEqual(mock_deposit_collection.insert_one.call_count, 1)
        insert_call = mock_deposit_collection.insert_one.call_args[0][0]
        self.assertEqual(insert_call["amount"], 1000)
        self.assertEqual(insert_call["location"], "คลังห้องเย็น")
        self.assertEqual(insert_call["reason"], "เงินทอน")
        self.assertEqual(insert_call["status"], "replenishment_started")
        self.assertEqual(insert_call["deposit_request_id"], "d-fedcba09")
        
        # No status update on success start (only insert initial doc)
        self.assertEqual(mock_deposit_collection.update_one.call_count, 0)
        
        # No transaction inserted at start step
        self.assertEqual(mock_transactions_collection.insert_one.call_count, 0)
        
        # Verify reply message was sent
        self.mock_line_bot_api.reply_message.assert_called_once()
        reply_args = self.mock_line_bot_api.reply_message.call_args
        self.assertIsInstance(reply_args[0][1], TemplateSendMessage)
        self.assertIn("เริ่มต้นการฝากเงิน", reply_args[0][1].template.text)
    
    @patch('handlers.deposit_requests_collection')
    @patch('handlers.transactions_collection')
    @patch('handlers.requests.post')
    @patch('handlers.now_bangkok_and_utc')
    @patch('handlers.build_correlation_headers')
    @patch('handlers.get_rest_api_ci_base_for_branch')
    @patch('handlers.uuid')
    def test_deposit_cold_storage_api_error(
        self,
        mock_uuid,
        mock_get_base,
        mock_build_headers,
        mock_now_bkk,
        mock_requests_post,
        mock_transactions_collection,
        mock_deposit_collection
    ):
        """Test deposit to Klangfrozen branch with API error"""
        # Setup mocks
        user_id = "test_user_456"
        reset_state(user_id)
        user_session[user_id]["state"] = "waiting_for_location_deposit"
        user_session[user_id]["amount"] = "750"
        user_session[user_id]["reason"] = "daily_sales"
        user_session[user_id]["location"] = "cold_storage"
        
        # Mock UUID
        mock_uuid_obj = Mock()
        mock_uuid_obj.hex = "fedcba0987654321"
        mock_uuid.uuid4.return_value = mock_uuid_obj
        
        # Mock time
        mock_now_bkk.return_value = (
            datetime(2024, 1, 15, 11, 0, 0),
            datetime(2024, 1, 15, 4, 0, 0)
        )
        
        # Mock API base URL
        mock_get_base.return_value = "http://10.0.0.15:5000"
        
        # Mock correlation headers
        mock_build_headers.return_value = (
            {"X-Trace-Id": "t-abcdef12"},
            {"trace_id": "t-abcdef12", "request_id": "r-21fedcba", "sale_id": "d-fedcba09"}
        )
        
        # Mock API error - use real requests exception
        import requests
        mock_requests_post.side_effect = requests.exceptions.Timeout("Request timeout")
        
        # Mock MongoDB collections
        mock_deposit_collection.insert_one = Mock()
        mock_deposit_collection.update_one = Mock()
        
        # Setup postback event
        self.mock_event.postback.data = f"select_location|cold_storage|{user_id}"
        
        # Execute
        handle_postback(self.mock_event, self.mock_line_bot_api)
        
        # Verify deposit request was inserted
        self.assertEqual(mock_deposit_collection.insert_one.call_count, 1)
        insert_call = mock_deposit_collection.insert_one.call_args[0][0]
        self.assertEqual(insert_call["reason"], "ฝากยอดขาย")
        
        # Verify status was updated to error
        self.assertEqual(mock_deposit_collection.update_one.call_count, 1)
        update_call = mock_deposit_collection.update_one.call_args
        self.assertEqual(update_call[0][1]["$set"]["status"], "error")
        
        # Verify transaction was NOT inserted
        self.assertEqual(mock_transactions_collection.insert_one.call_count, 0)
        
        # Verify error message was sent
        self.mock_line_bot_api.reply_message.assert_called_once()
        reply_args = self.mock_line_bot_api.reply_message.call_args
        self.assertIn("เกิดข้อผิดพลาด", reply_args[0][1].text)
    
    @patch('handlers.deposit_requests_collection')
    @patch('handlers.now_bangkok_and_utc')
    @patch('handlers.build_correlation_headers')
    @patch('handlers.get_rest_api_ci_base_for_branch')
    @patch('handlers.uuid')
    def test_reason_mapping(
        self,
        mock_uuid,
        mock_get_base,
        mock_build_headers,
        mock_now_bkk,
        mock_deposit_collection
    ):
        """Test reason code mapping to readable text"""
        # Setup mocks
        user_id = "test_user_789"
        reset_state(user_id)
        user_session[user_id]["state"] = "waiting_for_location_deposit"
        user_session[user_id]["amount"] = "200"
        user_session[user_id]["location"] = "noniko"
        
        # Test different reason codes
        test_cases = [
            ("change", "เงินทอน"),
            ("daily_sales", "ฝากยอดขาย"),
            ("other_reason", "other_reason"),  # Custom reason should pass through
        ]
        
        for reason_code, expected_reason_text in test_cases:
            with self.subTest(reason_code=reason_code):
                user_session[user_id]["reason"] = reason_code
                
                # Mock UUID
                mock_uuid_obj = Mock()
                mock_uuid_obj.hex = "test1234567890"
                mock_uuid.uuid4.return_value = mock_uuid_obj
                
                # Mock time
                mock_now_bkk.return_value = (
                    datetime(2024, 1, 15, 12, 0, 0),
                    datetime(2024, 1, 15, 5, 0, 0)
                )
                
                # Mock API base URL
                mock_get_base.return_value = "http://10.0.0.14:5000"
                
                # Mock correlation headers
                mock_build_headers.return_value = (
                    {"X-Trace-Id": "t-test"},
                    {"trace_id": "t-test", "request_id": "r-test", "sale_id": "d-test"}
                )
                
                # Mock successful API response
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"success": True}
                mock_response.raise_for_status = Mock()
                
                with patch('handlers.requests.post') as mock_requests_post:
                    mock_requests_post.return_value = mock_response
                    
                    # Setup postback event
                    self.mock_event.postback.data = f"select_location|noniko|{user_id}"
                    
                    # Execute
                    handle_postback(self.mock_event, self.mock_line_bot_api)
                    
                    # Verify reason was mapped correctly
                    if mock_deposit_collection.insert_one.called:
                        insert_call = mock_deposit_collection.insert_one.call_args[0][0]
                        self.assertEqual(insert_call["reason"], expected_reason_text)
                        self.assertEqual(insert_call["reason_code"], reason_code)
                    
                    # Reset mocks for next iteration
                    mock_deposit_collection.reset_mock()
                    self.mock_line_bot_api.reset_mock()
                    user_session.clear()
                    reset_state(user_id)


if __name__ == '__main__':
    unittest.main()

