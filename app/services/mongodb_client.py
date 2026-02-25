"""
MongoDB Client Service
Updates creditor data in MongoDB when responses are received
"""

from typing import Optional, Dict, Any
from datetime import datetime
import structlog
from app.services.monitoring.circuit_breakers import get_mongodb_breaker, CircuitBreakerError

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
        reference_numbers: Optional[list] = None,
        extraction_confidence: Optional[float] = None
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
                # Handle "LastName, FirstName" format (common in German documents)
                name_to_parse = client_name.strip()
                if ',' in name_to_parse:
                    # "Stockhöfer, Tobias" → first_name="Tobias", last_name="Stockhöfer"
                    parts = [p.strip() for p in name_to_parse.split(',', 1)]
                    if len(parts) == 2:
                        last_name, first_name = parts[0], parts[1]
                        logger.info("name_format_detected", format="LastName, FirstName",
                                   first_name=first_name, last_name=last_name)
                    else:
                        name_parts = name_to_parse.split(None, 1)
                        first_name, last_name = (name_parts[0], name_parts[1]) if len(name_parts) == 2 else (name_to_parse, "")
                else:
                    # Standard "FirstName LastName" format
                    name_parts = name_to_parse.split(None, 1)
                    first_name, last_name = (name_parts[0], name_parts[1]) if len(name_parts) == 2 else (name_to_parse, "")

                if first_name and last_name:
                    # Try exact match first (handles umlauts correctly)
                    client = clients_collection.find_one({
                        'firstName': first_name,
                        'lastName': last_name
                    })

                    # If not found, try case-insensitive with collation (works with umlauts)
                    if not client:
                        client = clients_collection.find_one(
                            {
                                'firstName': first_name,
                                'lastName': last_name
                            },
                            collation={'locale': 'de', 'strength': 2}  # Case-insensitive for German
                        )

                    if client:
                        logger.info("client_found", method="name", client_name=client_name)
                else:
                    # Try full name match in either field using collation for umlauts
                    client = clients_collection.find_one(
                        {
                            '$or': [
                                {'firstName': client_name},
                                {'lastName': client_name}
                            ]
                        },
                        collation={'locale': 'de', 'strength': 2}
                    )
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

                # Email matching (exact, contains, or domain match)
                if creditor_email and cred.get('sender_email'):
                    cred_email = cred.get('sender_email', '').lower().strip()
                    search_email = creditor_email.lower().strip()

                    # Strategy 1: Check if either contains the other (handles partial matches)
                    email_match = (search_email in cred_email) or (cred_email in search_email)

                    # Strategy 2: Domain matching (same company, different email address)
                    # e.g., inkasso@sparkasse.de vs forderungsmanagement@sparkasse.de
                    if not email_match and '@' in cred_email and '@' in search_email:
                        cred_domain = cred_email.split('@')[-1]
                        search_domain = search_email.split('@')[-1]
                        if cred_domain == search_domain:
                            email_match = True
                            logger.info("domain_match",
                                       cred_email=cred_email,
                                       search_email=search_email,
                                       domain=cred_domain)

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
            # NOTE: We set current_debt_amount (new from response), NOT claim_amount (original from document).
            # claim_amount is the original debt from the Forderungsaufstellung and must never be overwritten.
            idx = matched_creditor_index
            update_data = {
                f'final_creditor_list.{idx}.current_debt_amount': new_debt_amount,
                f'final_creditor_list.{idx}.creditor_response_amount': new_debt_amount,
                f'final_creditor_list.{idx}.amount_source': 'creditor_response',
                f'final_creditor_list.{idx}.response_received_at': datetime.utcnow(),
                f'final_creditor_list.{idx}.contact_status': 'responded',
                f'final_creditor_list.{idx}.last_contacted_at': datetime.utcnow()
            }

            if extraction_confidence is not None:
                update_data[f'final_creditor_list.{idx}.extraction_confidence'] = extraction_confidence

            if response_text:
                update_data[f'final_creditor_list.{idx}.creditor_response_text'] = response_text

            if reference_numbers:
                update_data[f'final_creditor_list.{idx}.response_reference_numbers'] = reference_numbers

            # Step 4: Update MongoDB with circuit breaker
            breaker = get_mongodb_breaker()
            try:
                result = breaker.call(
                    clients_collection.update_one,
                    {'_id': client['_id']},
                    {'$set': update_data}
                )
            except CircuitBreakerError:
                logger.error("mongodb_circuit_open", client_name=client_name)
                raise  # Let caller handle retry

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
            breaker = get_mongodb_breaker()
            try:
                client = breaker.call(
                    clients_collection.find_one,
                    {
                        '$or': [
                            {'zendesk_ticket_id': zendesk_ticket_id},
                            {'final_creditor_list.main_zendesk_ticket_id': zendesk_ticket_id}
                        ]
                    }
                )
            except CircuitBreakerError:
                logger.error("mongodb_circuit_open", operation="get_client_by_ticket")
                raise

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
            breaker = get_mongodb_breaker()
            try:
                client = breaker.call(
                    clients_collection.find_one,
                    {'aktenzeichen': aktenzeichen}
                )
            except CircuitBreakerError:
                logger.error("mongodb_circuit_open", operation="get_client_by_aktenzeichen")
                raise
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

            # Case-insensitive search with collation (works with umlauts)
            breaker = get_mongodb_breaker()
            try:
                # Try exact match first, then with German collation
                client = breaker.call(
                    clients_collection.find_one,
                    {'firstName': first_name, 'lastName': last_name}
                )
                if not client:
                    client = breaker.call(
                        clients_collection.find_one,
                        {'firstName': first_name, 'lastName': last_name},
                        collation={'locale': 'de', 'strength': 2}
                    )
            except CircuitBreakerError:
                logger.error("mongodb_circuit_open", operation="get_client_by_name")
                raise

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
