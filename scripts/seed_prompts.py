#!/usr/bin/env python
"""
Seed Initial Prompts

Migrates hardcoded prompts from codebase to database as version 1.
All seeded prompts start as is_active=True (ready for production).

Usage:
    python scripts/seed_prompts.py

Idempotent: skips prompts that already exist (checks task_type + name + version).
"""

from sqlalchemy import and_
from app.database import SessionLocal
from app.models.prompt_template import PromptTemplate

PROMPTS_TO_SEED = [
    {
        'task_type': 'classification',
        'name': 'email_intent',
        'version': 1,
        'system_prompt': None,
        'user_prompt_template': '''Klassifiziere die E-Mail-Intent in eine der folgenden Kategorien:

1. debt_statement - Gläubigerantwort mit Forderungsbetrag oder Schuldenstatus
2. payment_plan - Zahlungsplan-Vorschlag oder Bestätigung
3. rejection - Ablehnung oder Widerspruch der Forderung
4. inquiry - Frage die manuelle Antwort erfordert
5. auto_reply - Abwesenheitsnotiz oder automatische Antwort
6. spam - Marketing, unrelated content

E-Mail:
Betreff: {{ subject }}
Text: {{ truncated_body }}

Antworte nur mit JSON:
{"intent": "debt_statement|payment_plan|rejection|inquiry|auto_reply|spam", "confidence": 0.0-1.0}''',
        'is_active': True,
        'created_by': 'system_migration',
        'description': 'Migrated from hardcoded intent_classifier.py',
        'model_name': 'claude-haiku-4-20250514',
        'temperature': 0.0,
        'max_tokens': 100
    },
    {
        'task_type': 'extraction',
        'name': 'email_body',
        'version': 1,
        'system_prompt': '''Du bist ein Experten-Assistent für eine deutsche Rechtsanwaltskanzlei, die sich auf Schuldnerberatung spezialisiert hat.

Deine Aufgabe ist es, eingehende E-Mails von Gläubigern zu analysieren und strukturierte Informationen zu extrahieren.

Die Kanzlei sendet Anfragen an Gläubiger im Namen ihrer Mandanten. Die Gläubiger antworten dann mit Informationen über Schulden.

Extrahiere die folgenden Informationen aus der E-Mail:

1. **is_creditor_reply**: Ist dies eine legitime Gläubiger-Antwort? (nicht Spam, nicht Auto-Reply, nicht Out-of-Office)
2. **client_name**: Der vollständige Name des Mandanten (die Person, die die Schulden hat). Suche nach Phrasen wie "Herr/Frau [Name]", "Mandant", "Schuldner"
3. **creditor_name**: Der Firmenname des Gläubigers (Bank, Versicherung, Telekom, Inkassobüro, Rechtsanwaltskanzlei, etc.)
   - **WICHTIG**: Der Gläubiger ist die Firma/Organisation, die die FORDERUNG hält
   - Oft steht der Gläubiger-Name in der **Signatur** der Email (z.B. "Mit freundlichen Grüßen, [Name], [Firma]")
   - Der Gläubiger-Name kann auch im Briefkopf, Footer oder in der Absender-Zeile stehen
   - Auch wenn die Email von einer persönlichen Adresse kommt (z.B. gmail.com), schaue in der Signatur nach der Firmenbezeichnung
4. **debt_amount**: Gesamtschulden in EUR. Suche nach "Forderung", "Betrag", "Schulden"
5. **reference_numbers**: Alle Referenznummern (Aktenzeichen, Kundennummer, Vertragsnummer, Rechnungsnummer)
6. **confidence**: Dein Vertrauen in die Extraktion (0.0 = sehr unsicher, 1.0 = sehr sicher)
7. **summary**: Kurze 1-2 Satz Zusammenfassung der E-Mail

**Wichtig**:
- Kundennamen können im Format "Nachname, Vorname" oder "Vorname Nachname" sein
- Normalisiere wenn möglich auf "Nachname, Vorname" Format
- **SUCHE IMMER in der Signatur nach dem Gläubiger-Namen** (z.B. "Mit freundlichen Grüßen, K. Capelle, awt Rechtsanwälte" → creditor_name = "awt Rechtsanwälte")
- Wenn du unsicher bist, setze confidence niedrig, aber gib trotzdem deine beste Schätzung ab
- Gib nur valides JSON zurück, das dem Schema entspricht

**Output Format** (NUR JSON, keine zusätzlichen Kommentare):
{
  "is_creditor_reply": true/false,
  "client_name": "Mustermann, Max" oder null,
  "creditor_name": "Sparkasse Bochum" oder null,
  "debt_amount": 1234.56 oder null,
  "reference_numbers": ["AZ-123", "KD-456"] oder [],
  "confidence": 0.85,
  "summary": "Kurze Zusammenfassung" oder null
}''',
        'user_prompt_template': '''Bitte extrahiere Informationen aus dieser E-Mail:

**Von**: {{ from_email }}
**Betreff**: {{ subject }}

**E-Mail Inhalt**:
{{ email_body }}

Gib die Antwort als JSON zurück (nur JSON, keine zusätzlichen Erklärungen):''',
        'is_active': True,
        'created_by': 'system_migration',
        'description': 'Migrated from hardcoded entity_extractor_claude.py',
        'model_name': 'claude-sonnet-4-5-20250514',
        'temperature': 0.1,
        'max_tokens': 1024
    },
    {
        'task_type': 'extraction',
        'name': 'pdf_scanned',
        'version': 1,
        'system_prompt': None,
        'user_prompt_template': '''Analysiere dieses deutsche Glaeubigerdokument und extrahiere die folgenden Informationen.

WICHTIGE REGELN:
1. Suche nach "Gesamtforderung" (Hauptbetrag) - dies ist der wichtigste Betrag
2. Akzeptiere auch Synonyme: "Forderungshoehe", "offener Betrag", "Gesamtsumme", "Schulden", "Restschuld"
3. Deutsche Zahlenformatierung: 1.234,56 EUR bedeutet 1234.56
4. Wenn keine explizite Gesamtforderung: Summiere "Hauptforderung" + "Zinsen" + "Kosten"

BEISPIELE (typische Formulierungen in Glaeubiger-Antworten):
- "Die Gesamtforderung betraegt 1.234,56 EUR" -> gesamtforderung: 1234.56
- "Offener Betrag: 2.500,00 EUR" -> gesamtforderung: 2500.00
- "Restschuld per 01.01.2026: 3.456,78 EUR" -> gesamtforderung: 3456.78
- "Hauptforderung 1.000 EUR, Zinsen 150,50 EUR, Kosten 84,00 EUR" -> gesamtforderung: 1234.50 (Summe)

EXTRAHIERE:
1. gesamtforderung: Gesamtforderungsbetrag in EUR (nur Zahl, z.B. 1234.56)
2. glaeubiger: Name des Glaeubigerers/der Firma (z.B. "XY Inkasso GmbH", "ABC Bank AG")
3. schuldner: Name des Schuldners/Kunden (z.B. "Max Mustermann", "Maria Mueller")
4. components: Falls Gesamtforderung nicht explizit, gib Aufschluesselung an

Gib NUR valides JSON in diesem exakten Format zurueck:
{
  "gesamtforderung": 1234.56,
  "glaeubiger": "Firmenname",
  "schuldner": "Kundenname",
  "components": {
    "hauptforderung": 1000.00,
    "zinsen": 150.56,
    "kosten": 84.00
  }
}

Wenn ein Feld nicht gefunden wird, nutze null. Fuer gesamtforderung gib null nur zurueck, wenn gar keine Betraege gefunden werden.''',
        'is_active': True,
        'created_by': 'system_migration',
        'description': 'Migrated from hardcoded pdf_extractor.py EXTRACTION_PROMPT',
        'model_name': 'claude-sonnet-4-5-20250514',
        'temperature': 0.1,
        'max_tokens': 2048
    },
    {
        'task_type': 'extraction',
        'name': 'image',
        'version': 1,
        'system_prompt': None,
        'user_prompt_template': '''Analysiere dieses Bild eines deutschen Glaeubiger-/Inkassodokuments.

Extrahiere die folgenden Informationen, falls sichtbar:
1. Gesamtforderung (Gesamtbetrag) - suche nach Waehrungsbetraegen in EUR
2. Falls kein Gesamtbetrag: Summiere Hauptforderung + Zinsen + Kosten
3. Glaeubiger (Name des Glaeubigerers/der Firma)
4. Schuldner (Name des Schuldners/Kunden)

WICHTIG:
- Akzeptiere Synonyme: "Schulden", "offener Betrag", "Restschuld", "Forderungshoehe"
- Deutsche Zahlenformatierung: 1.234,56 EUR bedeutet 1234.56
- Bei unleserlichen Stellen: null zurueckgeben statt raten

BEISPIELE:
- "Gesamtforderung: 1.234,56 EUR" -> 1234.56
- "Offener Betrag per 01.01.2026: 2.500 EUR" -> 2500.00

Gib NUR valides JSON zurueck:
{
  "gesamtforderung": 1234.56,
  "glaeubiger": "Firmenname",
  "schuldner": "Personenname"
}

Falls die Information nicht sichtbar ist oder das Bild kein relevantes Dokument zeigt:
{"gesamtforderung": null, "glaeubiger": null, "schuldner": null}''',
        'is_active': True,
        'created_by': 'system_migration',
        'description': 'Migrated from hardcoded image_extractor.py IMAGE_EXTRACTION_PROMPT',
        'model_name': 'claude-sonnet-4-5-20250514',
        'temperature': 0.1,
        'max_tokens': 1024
    }
]


def seed_initial_prompts():
    """
    Seed database with initial prompts from codebase.

    Idempotent: checks for existing versions before inserting.
    All prompts seeded as v1 with is_active=True.
    """
    db = SessionLocal()

    try:
        seeded_count = 0
        skipped_count = 0

        for prompt_data in PROMPTS_TO_SEED:
            # Check if prompt version already exists
            existing = db.query(PromptTemplate).filter(
                and_(
                    PromptTemplate.task_type == prompt_data['task_type'],
                    PromptTemplate.name == prompt_data['name'],
                    PromptTemplate.version == prompt_data['version']
                )
            ).first()

            if existing:
                print(
                    f"⏭️  Skipping {prompt_data['task_type']}.{prompt_data['name']} "
                    f"v{prompt_data['version']} - already exists"
                )
                skipped_count += 1
                continue

            # Create new prompt template
            prompt = PromptTemplate(**prompt_data)
            db.add(prompt)
            print(
                f"✅ Seeded {prompt_data['task_type']}.{prompt_data['name']} "
                f"v{prompt_data['version']} (active)"
            )
            seeded_count += 1

        # Commit all new prompts
        db.commit()

        print(f"\n{'='*60}")
        print(f"Initial prompt seeding complete!")
        print(f"  Seeded: {seeded_count} prompts")
        print(f"  Skipped: {skipped_count} prompts (already exist)")
        print(f"{'='*60}")

    except Exception as e:
        db.rollback()
        print(f"❌ Error seeding prompts: {e}")
        raise
    finally:
        db.close()


if __name__ == '__main__':
    seed_initial_prompts()
