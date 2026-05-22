"""File-based logging utilities for production debugging.

Provides functions to log API responses and other debug info to files
for production environments where logging may not be easily accessible.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def _file_logging_enabled():
    return getattr(settings, 'TIKTOK_FILE_LOGGING_ENABLED', True)


def get_debug_log_path():
    """Get the debug logs directory path."""
    logs_dir = Path(settings.BASE_DIR) / 'logs' / 'debug'
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def log_api_response(source, donation_id, endpoint, response_data, error=None):
    """Log API response to a file for production debugging.
    
    Args:
        source (str): Data source identifier (e.g., 'tiktok', 'google')
        donation_id (int): Donation ID
        endpoint (str): API endpoint called
        response_data (dict): Response data or None
        error (str): Error message if applicable
    """
    if not _file_logging_enabled():
        return

    try:
        logs_dir = get_debug_log_path()
        timestamp = datetime.now().isoformat()
        
        log_entry = {
            'timestamp': timestamp,
            'source': source,
            'donation_id': donation_id,
            'endpoint': endpoint,
            'response': response_data,
            'error': error,
        }
        
        # Create log file per donation
        log_file = logs_dir / f"{source}_donation_{donation_id}.jsonl"
        
        with open(log_file, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
            
        logger.debug(f"Logged {source} API response for donation {donation_id} to {log_file}")
    except Exception as e:
        logger.error(f"Failed to write debug log: {e}")


def read_debug_logs(source, donation_id):
    """Read all debug logs for a donation.
    
    Args:
        source (str): Data source identifier
        donation_id (int): Donation ID
        
    Returns:
        list: List of log entries (dicts)
    """
    if not _file_logging_enabled():
        return []

    try:
        logs_dir = get_debug_log_path()
        log_file = logs_dir / f"{source}_donation_{donation_id}.jsonl"
        
        if not log_file.exists():
            return []
        
        entries = []
        with open(log_file, 'r') as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))
        return entries
    except Exception as e:
        logger.error(f"Failed to read debug logs: {e}")
        return []
