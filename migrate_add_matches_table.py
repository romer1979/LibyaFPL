# -*- coding: utf-8 -*-
"""
Database Migration: Create team_league_matches table

Run this BEFORE running rebuild_all_standings.py

From Render Shell:
    python migrate_add_matches_table.py
"""

from app import app, db
from sqlalchemy import text

def migrate():
    print("=" * 60)
    print("  Database Migration: Create team_league_matches table")
    print("=" * 60)
    
    with app.app_context():
        # Check if table already exists
        try:
            result = db.session.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'team_league_matches')"
            ))
            exists = result.fetchone()[0]
            
            if exists:
                print("\n✅ Table 'team_league_matches' already exists. No migration needed.")
                return
        except Exception as e:
            print(f"Error checking table: {e}")
        
        # Create the table
        print("\nCreating 'team_league_matches' table...")
        try:
            db.session.execute(text("""
                CREATE TABLE team_league_matches (
                    id SERIAL PRIMARY KEY,
                    league_type VARCHAR(20) NOT NULL,
                    gameweek INTEGER NOT NULL,
                    team1_name VARCHAR(100) NOT NULL,
                    team2_name VARCHAR(100) NOT NULL,
                    team1_points INTEGER DEFAULT 0,
                    team2_points INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT unique_match UNIQUE (league_type, gameweek, team1_name, team2_name)
                )
            """))
            db.session.commit()
            print("✅ Table created successfully!")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error creating table: {e}")
            return
        
        # Verify
        result = db.session.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'team_league_matches'"
        ))
        if result.fetchone():
            print("\n✅ Table 'team_league_matches' verified!")


if __name__ == '__main__':
    migrate()
