/**
 * Sync Creditor Inquiry to Matcher Service
 *
 * Call this function from your creditorContactService.js after creating
 * side conversations to sync inquiry data to the PostgreSQL matching database.
 *
 * Usage:
 *   const { syncInquiryToMatcher } = require('./sync_inquiry_to_matcher');
 *
 *   await syncInquiryToMatcher({
 *     client: clientDoc,
 *     creditor: creditorObject,
 *     mainTicketId: mainZendeskTicketId
 *   });
 */

const axios = require('axios');

// Configuration
const MATCHER_API_URL = process.env.MATCHER_API_URL || 'http://localhost:8000';

/**
 * Sync a single creditor inquiry to the matcher service
 *
 * @param {Object} options
 * @param {Object} options.client - Full MongoDB client document
 * @param {Object} options.creditor - Creditor object from final_creditor_list
 * @param {String} options.mainTicketId - Main Zendesk ticket ID
 * @returns {Promise<Object>} Created inquiry response
 */
async function syncInquiryToMatcher({ client, creditor, mainTicketId }) {
  try {
    // Extract client name (try different formats)
    const clientName = getClientName(client);

    if (!clientName) {
      throw new Error('Could not extract client name from client document');
    }

    // Build the request payload matching PostgreSQL schema
    const inquiryData = {
      // Client information
      client_name: clientName,
      client_reference_number: client.aktenzeichen || null,

      // Creditor information
      creditor_name: creditor.sender_name || creditor.actual_creditor,
      creditor_email: creditor.sender_email,
      creditor_address: creditor.sender_address || null,

      // Debt information
      debt_amount: creditor.claim_amount || null,
      reference_numbers: creditor.reference_number ? [creditor.reference_number] : [],

      // Zendesk tracking
      zendesk_ticket_id: mainTicketId,
      zendesk_side_conversation_id: creditor.side_conversation_id || null,

      // Timing
      sent_at: creditor.email_sent_at || creditor.side_conversation_created_at || new Date().toISOString(),

      // Additional metadata
      contact_status: creditor.contact_status || null,
      document_url: creditor.first_round_document_url || null,
      notes: buildNotes(creditor)
    };

    console.log(`[Matcher Sync] Syncing inquiry for ${clientName} → ${creditor.sender_name}`);

    // Send to matcher API
    const response = await axios.post(
      `${MATCHER_API_URL}/api/v1/inquiries/`,
      inquiryData,
      {
        headers: {
          'Content-Type': 'application/json'
        },
        timeout: 10000 // 10 second timeout
      }
    );

    console.log(`[Matcher Sync] ✅ Successfully synced inquiry - ID: ${response.data.id}`);

    return {
      success: true,
      inquiry_id: response.data.id,
      data: response.data
    };

  } catch (error) {
    if (error.response?.status === 409) {
      // Duplicate - already exists
      console.log(`[Matcher Sync] ℹ️  Inquiry already exists (duplicate) - ${creditor.sender_email}`);
      return {
        success: true,
        duplicate: true,
        message: 'Inquiry already exists'
      };
    }

    console.error(`[Matcher Sync] ❌ Error syncing inquiry:`, error.message);
    if (error.response?.data) {
      console.error(`[Matcher Sync] Response error:`, error.response.data);
    }

    return {
      success: false,
      error: error.message,
      details: error.response?.data
    };
  }
}

/**
 * Sync all creditors from a client to the matcher service
 *
 * @param {Object} client - Full MongoDB client document
 * @param {String} mainTicketId - Main Zendesk ticket ID
 * @returns {Promise<Object>} Summary of sync results
 */
async function syncAllInquiriesForClient(client, mainTicketId) {
  if (!client.final_creditor_list || client.final_creditor_list.length === 0) {
    console.log('[Matcher Sync] No creditors to sync');
    return {
      success: true,
      synced: 0,
      skipped: 0,
      errors: 0
    };
  }

  console.log(`[Matcher Sync] Syncing ${client.final_creditor_list.length} creditors for client ${getClientName(client)}`);

  let synced = 0;
  let skipped = 0;
  let errors = 0;

  // Sync each creditor with email sent
  for (const creditor of client.final_creditor_list) {
    // Only sync creditors where email was actually sent
    if (!creditor.email_sent_at && !creditor.side_conversation_id) {
      console.log(`[Matcher Sync] Skipping ${creditor.sender_name} - no email sent yet`);
      skipped++;
      continue;
    }

    const result = await syncInquiryToMatcher({
      client,
      creditor,
      mainTicketId
    });

    if (result.success) {
      if (result.duplicate) {
        skipped++;
      } else {
        synced++;
      }
    } else {
      errors++;
    }

    // Small delay to avoid overwhelming the API
    await new Promise(resolve => setTimeout(resolve, 100));
  }

  console.log(`[Matcher Sync] Complete - Synced: ${synced}, Skipped: ${skipped}, Errors: ${errors}`);

  return {
    success: errors === 0,
    synced,
    skipped,
    errors
  };
}

/**
 * Extract client name from MongoDB client document
 * Tries different field combinations
 */
function getClientName(client) {
  // Try full name first
  if (client.full_name) {
    return client.full_name;
  }

  // Try combining first and last name
  if (client.first_name && client.last_name) {
    return `${client.last_name}, ${client.first_name}`;
  }

  // Try just last name and first name separately
  if (client.last_name) {
    return client.first_name ? `${client.first_name} ${client.last_name}` : client.last_name;
  }

  // Try name field if it exists
  if (client.name) {
    return client.name;
  }

  return null;
}

/**
 * Build notes string from creditor metadata
 */
function buildNotes(creditor) {
  const notes = [];

  if (creditor.first_round_document_filename) {
    notes.push(`Document: ${creditor.first_round_document_filename}`);
  }

  if (creditor.is_representative) {
    notes.push(`Representative for: ${creditor.actual_creditor}`);
  }

  if (creditor.ai_confidence) {
    notes.push(`AI Confidence: ${(creditor.ai_confidence * 100).toFixed(0)}%`);
  }

  if (creditor.source_document) {
    notes.push(`Source: ${creditor.source_document}`);
  }

  return notes.length > 0 ? notes.join(' | ') : null;
}

module.exports = {
  syncInquiryToMatcher,
  syncAllInquiriesForClient
};
