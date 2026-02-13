"""
Market2Agent — Database Package
Re-exports for convenience.
"""
from app.db.neo4j import get_driver, get_session, init_schema, close

# Alias used by auth.py and dashboard.py
get_neo4j_driver = get_driver
