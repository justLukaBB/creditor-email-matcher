"""
MongoDB Client Service
Updates creditor data in MongoDB when responses are received
"""

from typing import Optional, Dict, Any
from datetime import datetime
import structlog

logger = structlog.get_logger()

# Import MongoDB client (pymongo)
try:
    from pymongo import MongoClient
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    logger.warning("mongodb_unavailable", reason="pymongo_not_installed")


class MongoDBService:
    """
    Service to interact with MongoDB for updating creditor information
    """

    def __init__(self):
        """Initialize MongoDB connection"""
        self.client = None
        self.db = None
        self.mongodb_url = None
        self.mongodb_database = None
        self._initialized = False

    def _lazy_init(self):
        """Lazy initialization to ensure settings are loaded"""
        if self._initialized:
            return

        self._initialized = True

        # Import settings here to avoid circular imports
        from app.config import settings

        self.mongodb_url = settings.mongodb_url
        self.mongodb_database = settings.mongodb_database

        if not MONGODB_AVAILABLE:
            logger.warning("mongodb_client_unavailable")
            return

        if not self.mongodb_url:
            logger.warning("mongodb_url_not_configured")
            return

        try:
            # Connect with SSL verification disabled for development (MongoDB Atlas)
            self.client = MongoClient(
                self.mongodb_url,
                tlsAllowInvalidCertificates=True,
                serverSelectionTimeoutMS=10000
            )
            self.db = self.client[self.mongodb_database]
            # Test connection
            self.client.admin.command('ping')
            logger.info("mongodb_connected", database=self.mongodb_database)
        except Exception as e:
            logger.error("mongodb_connection_failed", error=str(e))
            self.client = None
            self.db = None

    def is_available(self) -> bool:
        """Check if MongoDB is available"""
        self._lazy_init()
        return self.client is not None and self.db is not None

    def update_creditor_debt_amount(
        self,
        client_name: str,
        client_aktenzeichen: Optional[str],
        creditor_email: str,
        creditor_name: str,
        new_debt_amount: float,
        response_text: Optional[str] = None,
        reference_numbers: Optional[list] = None
    ) -> bool:
        """
        Update creditor's debt amount in MongoDB based on client and creditor identification

        Args:
            client_name: Full name of client (e.g., "Luka Scuric" or "test user 2429")
            client_aktenzeichen: Client case number (e.g., "542900")
            creditor_email: Email of the creditor
            creditor_name: Name of the creditor
            new_debt_amount: Updated debt amount from creditor response
            response_text: Optional response text/summary
            reference_numbers: Optional reference numbers from response

        Returns:
            True if update successful, False otherwise
        """
        if not self.is_available():
            logger.warning("mongodb_update_skipped", reason="not_available")
            return False

        try:
            clients_collection = self.db['clients']

            # Step 1: Find the client
            # Try by aktenzeichen first, then by name
            client = None

            if client_aktenzeichen:
                client = clients_collection.find_one({'aktenzeichen': client_aktenzeichen})
                if client:
                    logger.info("client_found", method="aktenzeichen", aktenzeichen=client_aktenzeichen)

            if not client and client_name:
                # Try to split name into first and last
                name_parts = client_name.strip().split(None, 1)  # Split on first space
                if len(name_parts) == 2:
                    first_name, last_name = name_parts
                    client = clients_collection.find_one({
                        'firstName': {'$regex': f'^{first_name}$', '$options': 'i'},
                        'lastName': {'$regex': f'^{last_name}$', '$options': 'i'}
                    })
                    if client:
                        logger.info("client_found", method="name", client_name=client_name)
                else:
                    # Try full name match in either field
                    client = clients_collection.find_one({
                        '$or': [
                            {'firstName': {'$regex': client_name, '$options': 'i'}},
                            {'lastName': {'$regex': client_name, '$options': 'i'}}
                        ]
                    })
                    if client:
                        logger.info("client_found", method="partial_name", client_name=client_name)

            if not client:
                logger.warning("client_not_found", client_name=client_name, aktenzeichen=client_aktenzeichen)
                return False

            # Step 2: Find the creditor in final_creditor_list
            creditors = client.get('final_creditor_list', [])
            matched_creditor_index = None

            for idx, cred in enumerate(creditors):
                # Match by email (primary) or name (fallback with fuzzy matching)
                email_match = False
                name_match = False

                # Email matching (exact or contains)
                if creditor_email and cred.get('sender_email'):
                    cred_email = cred.get('sender_email', '').lower().strip()
                    search_email = creditor_email.lower().strip()
                    # Check if either contains the other (handles partial matches)
                    email_match = (search_email in cred_email) or (cred_email in search_email)

                # Name matching (fuzzy - check if words overlap)
                if creditor_name and cred.get('sender_name'):
                    cred_name = cred.get('sender_name', '').lower().strip()
                    search_name = creditor_name.lower().strip()

                    # Extract significant words (length > 3) from both names
                    cred_words = set(word for word in cred_name.split() if len(word) > 3)
                    search_words = set(word for word in search_name.split() if len(word) > 3)

                    # Check if any significant words match
                    if cred_words and search_words:
                        common_words = cred_words & search_words
                        if common_words:
                            name_match = True
                            logger.info("fuzzy_name_match", common_words=list(common_words))

                    # Also check if one name contains the other
                    if not name_match:
                        name_match = (search_name in cred_name) or (cred_name in search_name)

                if email_match or name_match:
                    matched_creditor_index = idx
                    logger.info("creditor_matched",
                               creditor_name=cred.get('sender_name'),
                               email_match=email_match,
                               name_match=name_match)
                    break

            if matched_creditor_index is None:
                logger.warning("creditor_not_found",
                              creditor_email=creditor_email,
                              creditor_name=creditor_name)
                return False

            # Step 3: Build update data
            update_data = {
                f'final_creditor_list.{matched_creditor_index}.claim_amount': new_debt_amount,
                f'final_creditor_list.{matched_creditor_index}.creditor_response_amount': new_debt_amount,
                f'final_creditor_list.{matched_creditor_index}.amount_source': 'creditor_response',
                f'final_creditor_list.{matched_creditor_index}.response_received_at': datetime.utcnow(),
                f'final_creditor_list.{matched_creditor_index}.contact_status': 'responded',
                f'final_creditor_list.{matched_creditor_index}.last_contacted_at': datetime.utcnow()
            }

            if response_text:
                update_data[f'final_creditor_list.{matched_creditor_index}.creditor_response_text'] = response_text

            if reference_numbers:
                update_data[f'final_creditor_list.{matched_creditor_index}.response_reference_numbers'] = reference_numbers

            # Step 4: Update MongoDB
            result = clients_collection.update_one(
                {'_id': client['_id']},
                {'$set': update_data}
            )

            if result.modified_count > 0:
                logger.info("mongodb_updated",
                           aktenzeichen=client.get('aktenzeichen'),
                           creditor_name=creditor_name,
                           debt_amount=new_debt_amount)
                return True
            else:
                logger.warning("mongodb_no_changes")
                return False

        except Exception as e:
            logger.error("mongodb_update_error", error=str(e), exc_info=True)
            return False

    def get_client_by_ticket(self, zendesk_ticket_id: str) -> Optional[Dict[str, Any]]:
        """
        Get client document by Zendesk ticket ID

        Args:
            zendesk_ticket_id: Main Zendesk ticket ID

        Returns:
            Client document or None
        """
        if not self.is_available():
            return None

        try:
            clients_collection = self.db['clients']

            # Search in both top-level zendesk_ticket_id and in final_creditor_list
            client = clients_collection.find_one({
                '$or': [
                    {'zendesk_ticket_id': zendesk_ticket_id},
                    {'final_creditor_list.main_zendesk_ticket_id': zendesk_ticket_id}
                ]
            })

            return client

        except Exception as e:
            logger.error("get_client_by_ticket_error", error=str(e))
            return None

    def get_client_by_aktenzeichen(self, aktenzeichen: str) -> Optional[Dict[str, Any]]:
        """
        Get client document by Aktenzeichen (case number)

        Args:
            aktenzeichen: Client case number (e.g., "1381_25")

        Returns:
            Client document or None
        """
        if not self.is_available():
            return None

        try:
            clients_collection = self.db['clients']
            client = clients_collection.find_one({'aktenzeichen': aktenzeichen})
            return client

        except Exception as e:
            logger.error("get_client_by_aktenzeichen_error", error=str(e))
            return None

    def get_client_by_name(self, first_name: str, last_name: str) -> Optional[Dict[str, Any]]:
        """
        Get client document by name (case-insensitive)

        Args:
            first_name: Client's first name
            last_name: Client's last name

        Returns:
            Client document or None
        """
        if not self.is_available():
            return None

        try:
            clients_collection = self.db['clients']

            # Case-insensitive search
            client = clients_collection.find_one({
                'firstName': {'$regex': f'^{first_name}$', '$options': 'i'},
                'lastName': {'$regex': f'^{last_name}$', '$options': 'i'}
            })

            return client

        except Exception as e:
            logger.error("get_client_by_name_error", error=str(e))
            return None

    def close(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            logger.info("mongodb_connection_closed")


# Global instance
mongodb_service = MongoDBService()
