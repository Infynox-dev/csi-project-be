"""
Seed script to populate Kalamela events from the official event list.
Run this script to delete existing events and add the 45 official events.
"""

import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')

async def seed_events():
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        print("Starting event seeding...")
        
        # Step 1: Delete existing data (in correct order due to foreign keys)
        print("Deleting existing participations...")
        await session.execute(text("DELETE FROM individual_event_participation"))
        await session.execute(text("DELETE FROM group_event_participation"))
        
        print("Deleting existing events...")
        await session.execute(text("DELETE FROM individual_event"))
        await session.execute(text("DELETE FROM group_event"))
        
        print("Deleting existing categories...")
        await session.execute(text("DELETE FROM event_category"))
        
        await session.commit()
        print("Existing data deleted.")
        
        # Step 2: Create event categories
        print("Creating event categories...")
        categories = [
            ("MUSIC", "Music events including solo, group performances, and instrumental"),
            ("FINE ARTS", "Fine arts events including drawing and painting"),
            ("STAGE EVENTS", "Stage performance events including drama and dance"),
            ("LITERARY EVENTS", "Literary events including poetry, essay, and extempore"),
        ]
        
        await session.execute(text("""
            INSERT INTO event_category (name, description, created_on, updated_on) VALUES
            (:name1, :desc1, NOW(), NOW()),
            (:name2, :desc2, NOW(), NOW()),
            (:name3, :desc3, NOW(), NOW()),
            (:name4, :desc4, NOW(), NOW())
        """), {
            "name1": categories[0][0], "desc1": categories[0][1],
            "name2": categories[1][0], "desc2": categories[1][1],
            "name3": categories[2][0], "desc3": categories[2][1],
            "name4": categories[3][0], "desc4": categories[3][1],
        })
        await session.commit()
        
        # Get category IDs
        result = await session.execute(text("SELECT id, name FROM event_category ORDER BY id"))
        category_map = {row[1]: row[0] for row in result.fetchall()}
        print(f"Categories created: {category_map}")
        
        # Step 3: Insert Individual Events (36 events)
        # Format: (name, category_id, is_mandatory, gender_restriction, seniority_restriction)
        # Gender: 'Male', 'Female', or None (any)
        # Seniority: 'Junior', 'Senior', or None (any)
        print("Creating individual events...")
        
        individual_events = [
            # MUSIC - Individual (18 events)
            ("Solo Eastern Jr Boys", category_map["MUSIC"], True, "Male", "Junior"),
            ("Solo Eastern Jr Girls", category_map["MUSIC"], True, "Female", "Junior"),
            ("Solo Eastern Sr Boys", category_map["MUSIC"], True, "Male", "Senior"),
            ("Solo Eastern Sr Girls", category_map["MUSIC"], True, "Female", "Senior"),
            ("Solo Western Jr Boys", category_map["MUSIC"], True, "Male", "Junior"),
            ("Solo Western Jr Girls", category_map["MUSIC"], True, "Female", "Junior"),
            ("Solo Western Sr Boys", category_map["MUSIC"], True, "Male", "Senior"),
            ("Solo Western Sr Girls", category_map["MUSIC"], True, "Female", "Senior"),
            ("Light music Boys", category_map["MUSIC"], True, "Male", None),  # Any seniority
            ("Light music Girls", category_map["MUSIC"], True, "Female", None),  # Any seniority
            ("Poetry Recitation", category_map["MUSIC"], False, None, None),  # Open to all
            ("Classical Music", category_map["MUSIC"], False, None, None),  # Open to all
            ("Karoke Song", category_map["MUSIC"], False, None, None),  # Open to all
            ("Organ Recital", category_map["MUSIC"], True, None, None),  # Open to all
            ("Instrumental Music Wind", category_map["MUSIC"], False, None, None),  # Open to all
            ("Instrumental Music String", category_map["MUSIC"], False, None, None),  # Open to all
            ("Instrumental Music Percussion", category_map["MUSIC"], False, None, None),  # Open to all
            ("Guitar", category_map["MUSIC"], False, None, None),  # Open to all
            
            # FINE ARTS - Individual (3 events) - All open to any gender/seniority
            ("Pencil drawing", category_map["FINE ARTS"], True, None, None),
            ("Cartoon", category_map["FINE ARTS"], True, None, None),
            ("Water Colour", category_map["FINE ARTS"], True, None, None),
            
            # STAGE EVENTS - Individual (3 events) - All open to any gender/seniority
            ("Fancy Dress", category_map["STAGE EVENTS"], True, None, None),
            ("Monoact", category_map["STAGE EVENTS"], True, None, None),
            ("Mimicry", category_map["STAGE EVENTS"], True, None, None),
            
            # LITERARY EVENTS - Individual (12 events)
            ("Poetry English", category_map["LITERARY EVENTS"], False, None, None),  # Open to all
            ("Poetry Malayalam", category_map["LITERARY EVENTS"], False, None, None),  # Open to all
            ("Short Story English", category_map["LITERARY EVENTS"], False, None, None),  # Open to all
            ("Short Story Malayalam", category_map["LITERARY EVENTS"], False, None, None),  # Open to all
            ("Essay Eng Jr", category_map["LITERARY EVENTS"], False, None, "Junior"),  # Junior only
            ("Essay Mal Jr", category_map["LITERARY EVENTS"], False, None, "Junior"),  # Junior only
            ("Essay Eng Sr", category_map["LITERARY EVENTS"], False, None, "Senior"),  # Senior only
            ("Essay Mal Sr", category_map["LITERARY EVENTS"], False, None, "Senior"),  # Senior only
            ("Extempore Mal Jr", category_map["LITERARY EVENTS"], True, None, "Junior"),  # Junior only
            ("Extempore Mal Sr", category_map["LITERARY EVENTS"], True, None, "Senior"),  # Senior only
            ("Extempore Eng Jr", category_map["LITERARY EVENTS"], True, None, "Junior"),  # Junior only
            ("Extempore Eng Sr", category_map["LITERARY EVENTS"], True, None, "Senior"),  # Senior only
        ]
        
        for name, cat_id, is_mandatory, gender, seniority in individual_events:
            await session.execute(text("""
                INSERT INTO individual_event (name, category_id, is_mandatory, is_active, gender_restriction, seniority_restriction, description, created_on)
                VALUES (:name, :cat_id, :is_mandatory, true, :gender, :seniority, NULL, NOW())
            """), {
                "name": name, 
                "cat_id": cat_id, 
                "is_mandatory": is_mandatory,
                "gender": gender,
                "seniority": seniority
            })
        
        await session.commit()
        print(f"Created {len(individual_events)} individual events.")
        
        # Step 4: Insert Group Events (9 events)
        # Format: (name, category_id, is_mandatory, gender, seniority, min_limit, max_limit, per_unit_limit)
        print("Creating group events...")
        
        group_events = [
            # MUSIC - Group (4 events) - All open to any gender/seniority
            ("Fusion", category_map["MUSIC"], False, None, None, 3, 8, 1),
            ("Group Song", category_map["MUSIC"], True, None, None, 3, 8, 1),
            ("Quartet", category_map["MUSIC"], True, None, None, 4, 4, 1),
            ("Octect", category_map["MUSIC"], False, None, None, 8, 8, 1),
            
            # STAGE EVENTS - Group (5 events) - All open to any gender/seniority
            ("Tableau", category_map["STAGE EVENTS"], False, None, None, 4, 8, 1),
            ("Mime", category_map["STAGE EVENTS"], False, None, None, 5, 8, 1),
            ("Kadhaprasangam", category_map["STAGE EVENTS"], False, None, None, 4, 8, 1),
            ("Skit", category_map["STAGE EVENTS"], False, None, None, 5, 10, 1),
            ("Margamkali", category_map["STAGE EVENTS"], False, None, None, 5, 9, 1),
        ]
        
        for name, cat_id, is_mandatory, gender, seniority, min_limit, max_limit, per_unit in group_events:
            await session.execute(text("""
                INSERT INTO group_event (name, category_id, is_mandatory, is_active, gender_restriction, seniority_restriction, 
                                        min_allowed_limit, max_allowed_limit, per_unit_allowed_limit, description, created_on)
                VALUES (:name, :cat_id, :is_mandatory, true, :gender, :seniority, :min_limit, :max_limit, :per_unit, NULL, NOW())
            """), {
                "name": name, 
                "cat_id": cat_id, 
                "is_mandatory": is_mandatory,
                "gender": gender,
                "seniority": seniority,
                "min_limit": min_limit,
                "max_limit": max_limit,
                "per_unit": per_unit
            })
        
        await session.commit()
        print(f"Created {len(group_events)} group events.")
        
        # Summary
        print("\n" + "="*80)
        print("SEEDING COMPLETE!")
        print("="*80)
        print(f"Categories: 4")
        print(f"Individual Events: {len(individual_events)}")
        print(f"Group Events: {len(group_events)}")
        print(f"Total Events: {len(individual_events) + len(group_events)}")
        print("="*80)
        
        # Print summary by category
        result = await session.execute(text("""
            SELECT ec.name, 
                   (SELECT COUNT(*) FROM individual_event ie WHERE ie.category_id = ec.id) as ind_count,
                   (SELECT COUNT(*) FROM group_event ge WHERE ge.category_id = ec.id) as grp_count
            FROM event_category ec
            ORDER BY ec.id
        """))
        
        print("\nEvents by Category:")
        for row in result.fetchall():
            print(f"  {row[0]}: {row[1]} individual, {row[2]} group")
        
        # Print events with restrictions
        print("\n" + "="*80)
        print("INDIVIDUAL EVENTS WITH RESTRICTIONS:")
        print("="*80)
        result = await session.execute(text("""
            SELECT name, gender_restriction, seniority_restriction, is_mandatory
            FROM individual_event
            ORDER BY id
        """))
        for row in result.fetchall():
            gender = row[1] or "Any"
            seniority = row[2] or "Any"
            mandatory = "M" if row[3] else "OP"
            print(f"  {row[0]:40} | Gender: {gender:6} | Seniority: {seniority:6} | {mandatory}")


if __name__ == "__main__":
    asyncio.run(seed_events())
