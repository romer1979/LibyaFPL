# -*- coding: utf-8 -*-
"""
Database Migration: Add total_fpl_points column

Run this BEFORE running rebuild_all_standings.py

From Render Shell:
    python migrate_add_fpl_points.py
"""

from app import app, db
from sqlalchemy import text

def migrate():
    print("=" * 60)
    print("  Database Migration: Add total_fpl_points column")
    print("=" * 60)
    
    with app.app_context():
        # Check if column already exists
        try:
            result = db.session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='team_league_standings' AND column_name='total_fpl_points'"
            ))
            exists = result.fetchone() is not None
            
            if exists:
                print("\n✅ Column 'total_fpl_points' already exists. No migration needed.")
                return
        except Exception as e:
            print(f"Error checking column: {e}")
        
        # Add the column
        print("\nAdding 'total_fpl_points' column...")
        try:
            db.session.execute(text(
                "ALTER TABLE team_league_standings ADD COLUMN total_fpl_points INTEGER DEFAULT 0"
            ))
            db.session.commit()
            print("✅ Column added successfully!")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error adding column: {e}")
            return
        
        # Verify
        result = db.session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='team_league_standings'"
        ))
        columns = [row[0] for row in result.fetchall()]
        print(f"\nCurrent columns: {columns}")


if __name__ == '__main__':
    migrate()
