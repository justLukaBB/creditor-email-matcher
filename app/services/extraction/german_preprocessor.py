"""
German Text Preprocessor for OCR and Extraction Pipeline

Normalizes Unicode representation and corrects common OCR errors in German text.
Conservative approach: Only apply corrections when dictionary validates result.
"""

import unicodedata
import re
from typing import Dict
import structlog
from spellchecker import SpellChecker

logger = structlog.get_logger(__name__)


class GermanTextPreprocessor:
    """
    Preprocesses German text for extraction pipeline.

    Handles:
    1. Unicode normalization (NFKC) for consistent Umlaut representation
    2. OCR error correction for Umlaut restoration (dictionary-validated only)
    3. Character substitution for name/address fields (3->e, 0->o, 1->l)
    """

    # OCR often loses umlauts, producing digraph spellings like "Mueller" or "Muller"
    # This map restores actual umlaut characters when dictionary validates
    UMLAUT_RESTORATIONS = {
        'ue': 'ü',  # Mueller -> Müller (with actual umlaut ü)
        'oe': 'ö',  # Goethe -> Göthe (with actual umlaut ö)
        'ae': 'ä',  # Baecker -> Bäcker (with actual umlaut ä)
    }

    # Digit-to-letter substitutions for name/address fields only
    NAME_DIGIT_SUBSTITUTIONS = {
        '3': 'e',
        '0': 'o',
        '1': 'l',
    }

    def __init__(self):
        """Initialize with German spell checker."""
        self.spell_checker = SpellChecker(language='de')
        logger.info("GermanTextPreprocessor initialized", language="de")

    def preprocess(self, text: str) -> str:
        """
        Main preprocessing pipeline.

        Args:
            text: Raw text that may contain decomposed Unicode or OCR errors

        Returns:
            Normalized and corrected text
        """
        if not text:
            return text

        # Step 1: Unicode normalization (NFKC for consistent Umlaut representation)
        normalized = unicodedata.normalize('NFKC', text)

        # Step 2: OCR error correction (conservative, dictionary-validated only)
        corrected = self._correct_ocr_errors(normalized)

        return corrected

    def _correct_ocr_errors(self, text: str) -> str:
        """
        Correct common OCR errors in German text.

        Only applies corrections when:
        1. Word contains OCR-corrupted umlaut pattern (ue, oe, ae)
        2. Restored version exists in German dictionary

        Args:
            text: Normalized text

        Returns:
            Text with OCR errors corrected where dictionary validates
        """
        words = text.split()
        corrected_words = []

        for word in words:
            corrected_word = self._try_restore_umlauts(word)
            corrected_words.append(corrected_word)

        return ' '.join(corrected_words)

    def _try_restore_umlauts(self, word: str) -> str:
        """
        Try to restore umlauts in a single word.

        Args:
            word: Single word that may have OCR-corrupted umlauts

        Returns:
            Corrected word if dictionary validates, original otherwise
        """
        # Try each umlaut restoration
        for digraph, umlaut in self.UMLAUT_RESTORATIONS.items():
            if digraph in word.lower():
                # Try replacing the digraph with actual umlaut
                # Preserve case: if original had capital, capitalize restored
                candidate = self._replace_preserving_case(word, digraph, umlaut)

                # Check if restored version is in dictionary
                if self._is_valid_german_word(candidate):
                    logger.info(
                        "OCR correction applied",
                        original=word,
                        corrected=candidate,
                        pattern=f"{digraph}->{umlaut}"
                    )
                    return candidate
                else:
                    logger.debug(
                        "OCR correction skipped (not in dictionary)",
                        original=word,
                        candidate=candidate,
                        pattern=f"{digraph}->{umlaut}"
                    )

        return word

    def _replace_preserving_case(self, word: str, digraph: str, umlaut: str) -> str:
        """
        Replace digraph with umlaut, preserving case.

        Args:
            word: Original word (e.g., "Mueller", "MUELLER")
            digraph: Digraph to replace (e.g., "ue")
            umlaut: Umlaut character to insert (e.g., "ü")

        Returns:
            Word with digraph replaced, case preserved
        """
        # Handle case variations
        if digraph.upper() in word:
            # Uppercase digraph -> uppercase umlaut
            return word.replace(digraph.upper(), umlaut.upper(), 1)
        elif digraph.capitalize() in word:
            # Capitalized digraph -> capitalized umlaut
            return word.replace(digraph.capitalize(), umlaut.upper(), 1)
        else:
            # Lowercase digraph -> lowercase umlaut
            return word.replace(digraph, umlaut, 1)

    def _is_valid_german_word(self, word: str) -> bool:
        """
        Check if word exists in German dictionary.

        Args:
            word: Word to validate

        Returns:
            True if word is in dictionary, False otherwise
        """
        # Remove punctuation for dictionary lookup
        clean_word = re.sub(r'[^\w\-]', '', word)

        # SpellChecker.known() returns set of known words
        return clean_word.lower() in self.spell_checker or clean_word in self.spell_checker

    def correct_name_field(self, text: str) -> str:
        """
        Apply digit-to-letter substitutions for name/address fields.

        USER DECISION: Only apply to name/address fields, NOT to amounts or reference numbers.

        Args:
            text: Name or address field text

        Returns:
            Text with digit substitutions applied (3->e, 0->o, 1->l)
        """
        if not text:
            return text

        result = text
        for digit, letter in self.NAME_DIGIT_SUBSTITUTIONS.items():
            result = result.replace(digit, letter)

        logger.debug(
            "Name field digit correction applied",
            original=text,
            corrected=result
        )

        return result
