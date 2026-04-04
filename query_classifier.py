"""
Query Classifier for Smart Scout Search.
Classifies user search queries into intent types (artist, genre, subject, freetext)
to enable per-API optimized search parameters.

Hybrid approach: local dictionary lookup first, Gemini Flash fallback for ambiguous queries.
"""

import logging
import os
import json
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("artwork-display-api.classifier")

@dataclass
class SearchIntent:
    """Structured search intent produced by the classifier."""
    query_type: str           # "artist" | "genre" | "subject" | "freetext"
    original_query: str       # What the user typed
    canonical_name: str       # Normalized form (e.g., "Vincent van Gogh")
    related_terms: list = field(default_factory=list)
    era_hint: str = ""        # Optional era context (e.g., "Post-Impressionism")
    confidence: float = 1.0   # 0.0–1.0 confidence in classification


# ---------------------------------------------------------------------------
# Known Artists Dictionary (~200 entries)
# Keys are lowercase normalized forms; values are (canonical_name, era_hint)
# ---------------------------------------------------------------------------
KNOWN_ARTISTS = {
    # Dutch & Flemish Masters
    "van gogh": ("Vincent van Gogh", "Post-Impressionism"),
    "vincent van gogh": ("Vincent van Gogh", "Post-Impressionism"),
    "rembrandt": ("Rembrandt van Rijn", "Dutch Golden Age"),
    "rembrandt van rijn": ("Rembrandt van Rijn", "Dutch Golden Age"),
    "vermeer": ("Johannes Vermeer", "Dutch Golden Age"),
    "johannes vermeer": ("Johannes Vermeer", "Dutch Golden Age"),
    "jan vermeer": ("Johannes Vermeer", "Dutch Golden Age"),
    "rubens": ("Peter Paul Rubens", "Baroque"),
    "peter paul rubens": ("Peter Paul Rubens", "Baroque"),
    "hieronymus bosch": ("Hieronymus Bosch", "Northern Renaissance"),
    "bosch": ("Hieronymus Bosch", "Northern Renaissance"),
    "jan van eyck": ("Jan van Eyck", "Northern Renaissance"),
    "van eyck": ("Jan van Eyck", "Northern Renaissance"),
    "bruegel": ("Pieter Bruegel the Elder", "Northern Renaissance"),
    "pieter bruegel": ("Pieter Bruegel the Elder", "Northern Renaissance"),
    "frans hals": ("Frans Hals", "Dutch Golden Age"),

    # Italian Masters
    "leonardo da vinci": ("Leonardo da Vinci", "Renaissance"),
    "da vinci": ("Leonardo da Vinci", "Renaissance"),
    "leonardo": ("Leonardo da Vinci", "Renaissance"),
    "michelangelo": ("Michelangelo Buonarroti", "Renaissance"),
    "raphael": ("Raphael", "Renaissance"),
    "raffaello": ("Raphael", "Renaissance"),
    "caravaggio": ("Caravaggio", "Baroque"),
    "botticelli": ("Sandro Botticelli", "Renaissance"),
    "sandro botticelli": ("Sandro Botticelli", "Renaissance"),
    "titian": ("Titian", "Renaissance"),
    "tintoretto": ("Tintoretto", "Renaissance"),
    "giotto": ("Giotto di Bondone", "Proto-Renaissance"),
    "bernini": ("Gian Lorenzo Bernini", "Baroque"),
    "canaletto": ("Canaletto", "Rococo"),
    "tiepolo": ("Giovanni Battista Tiepolo", "Rococo"),
    "modigliani": ("Amedeo Modigliani", "Modernism"),
    "amedeo modigliani": ("Amedeo Modigliani", "Modernism"),

    # French Masters
    "monet": ("Claude Monet", "Impressionism"),
    "claude monet": ("Claude Monet", "Impressionism"),
    "renoir": ("Pierre-Auguste Renoir", "Impressionism"),
    "pierre-auguste renoir": ("Pierre-Auguste Renoir", "Impressionism"),
    "degas": ("Edgar Degas", "Impressionism"),
    "edgar degas": ("Edgar Degas", "Impressionism"),
    "cezanne": ("Paul Cézanne", "Post-Impressionism"),
    "paul cezanne": ("Paul Cézanne", "Post-Impressionism"),
    "cézanne": ("Paul Cézanne", "Post-Impressionism"),
    "paul cézanne": ("Paul Cézanne", "Post-Impressionism"),
    "manet": ("Édouard Manet", "Impressionism"),
    "edouard manet": ("Édouard Manet", "Impressionism"),
    "toulouse-lautrec": ("Henri de Toulouse-Lautrec", "Post-Impressionism"),
    "henri de toulouse-lautrec": ("Henri de Toulouse-Lautrec", "Post-Impressionism"),
    "gauguin": ("Paul Gauguin", "Post-Impressionism"),
    "paul gauguin": ("Paul Gauguin", "Post-Impressionism"),
    "seurat": ("Georges Seurat", "Post-Impressionism"),
    "georges seurat": ("Georges Seurat", "Post-Impressionism"),
    "delacroix": ("Eugène Delacroix", "Romanticism"),
    "eugene delacroix": ("Eugène Delacroix", "Romanticism"),
    "david": ("Jacques-Louis David", "Neoclassicism"),
    "jacques-louis david": ("Jacques-Louis David", "Neoclassicism"),
    "ingres": ("Jean-Auguste-Dominique Ingres", "Neoclassicism"),
    "courbet": ("Gustave Courbet", "Realism"),
    "gustave courbet": ("Gustave Courbet", "Realism"),
    "poussin": ("Nicolas Poussin", "Baroque"),
    "nicolas poussin": ("Nicolas Poussin", "Baroque"),
    "watteau": ("Antoine Watteau", "Rococo"),
    "fragonard": ("Jean-Honoré Fragonard", "Rococo"),
    "boucher": ("François Boucher", "Rococo"),
    "rodin": ("Auguste Rodin", "Impressionism"),
    "duchamp": ("Marcel Duchamp", "Dadaism"),
    "matisse": ("Henri Matisse", "Fauvism"),
    "henri matisse": ("Henri Matisse", "Fauvism"),
    "rousseau": ("Henri Rousseau", "Post-Impressionism"),
    "henri rousseau": ("Henri Rousseau", "Post-Impressionism"),

    # Spanish Masters
    "picasso": ("Pablo Picasso", "Cubism"),
    "pablo picasso": ("Pablo Picasso", "Cubism"),
    "dali": ("Salvador Dalí", "Surrealism"),
    "salvador dali": ("Salvador Dalí", "Surrealism"),
    "dalí": ("Salvador Dalí", "Surrealism"),
    "salvador dalí": ("Salvador Dalí", "Surrealism"),
    "goya": ("Francisco Goya", "Romanticism"),
    "francisco goya": ("Francisco Goya", "Romanticism"),
    "velazquez": ("Diego Velázquez", "Baroque"),
    "velázquez": ("Diego Velázquez", "Baroque"),
    "diego velazquez": ("Diego Velázquez", "Baroque"),
    "diego velázquez": ("Diego Velázquez", "Baroque"),
    "el greco": ("El Greco", "Mannerism"),
    "miro": ("Joan Miró", "Surrealism"),
    "joan miro": ("Joan Miró", "Surrealism"),
    "miró": ("Joan Miró", "Surrealism"),

    # German / Austrian / Swiss
    "durer": ("Albrecht Dürer", "Northern Renaissance"),
    "dürer": ("Albrecht Dürer", "Northern Renaissance"),
    "albrecht durer": ("Albrecht Dürer", "Northern Renaissance"),
    "albrecht dürer": ("Albrecht Dürer", "Northern Renaissance"),
    "holbein": ("Hans Holbein the Younger", "Northern Renaissance"),
    "klimt": ("Gustav Klimt", "Art Nouveau"),
    "gustav klimt": ("Gustav Klimt", "Art Nouveau"),
    "schiele": ("Egon Schiele", "Expressionism"),
    "egon schiele": ("Egon Schiele", "Expressionism"),
    "klee": ("Paul Klee", "Expressionism"),
    "paul klee": ("Paul Klee", "Expressionism"),
    "kandinsky": ("Wassily Kandinsky", "Abstract Art"),
    "wassily kandinsky": ("Wassily Kandinsky", "Abstract Art"),
    "caspar david friedrich": ("Caspar David Friedrich", "Romanticism"),
    "friedrich": ("Caspar David Friedrich", "Romanticism"),
    "max ernst": ("Max Ernst", "Surrealism"),
    "kirchner": ("Ernst Ludwig Kirchner", "Expressionism"),

    # British
    "turner": ("J.M.W. Turner", "Romanticism"),
    "j.m.w. turner": ("J.M.W. Turner", "Romanticism"),
    "william turner": ("J.M.W. Turner", "Romanticism"),
    "constable": ("John Constable", "Romanticism"),
    "john constable": ("John Constable", "Romanticism"),
    "gainsborough": ("Thomas Gainsborough", "Rococo"),
    "william blake": ("William Blake", "Romanticism"),
    "francis bacon": ("Francis Bacon", "Expressionism"),
    "lucian freud": ("Lucian Freud", "Realism"),
    "hockney": ("David Hockney", "Pop Art"),
    "david hockney": ("David Hockney", "Pop Art"),
    "banksy": ("Banksy", "Street Art"),

    # American
    "warhol": ("Andy Warhol", "Pop Art"),
    "andy warhol": ("Andy Warhol", "Pop Art"),
    "pollock": ("Jackson Pollock", "Abstract Expressionism"),
    "jackson pollock": ("Jackson Pollock", "Abstract Expressionism"),
    "rothko": ("Mark Rothko", "Abstract Expressionism"),
    "mark rothko": ("Mark Rothko", "Abstract Expressionism"),
    "de kooning": ("Willem de Kooning", "Abstract Expressionism"),
    "willem de kooning": ("Willem de Kooning", "Abstract Expressionism"),
    "basquiat": ("Jean-Michel Basquiat", "Neo-Expressionism"),
    "jean-michel basquiat": ("Jean-Michel Basquiat", "Neo-Expressionism"),
    "edward hopper": ("Edward Hopper", "Realism"),
    "hopper": ("Edward Hopper", "Realism"),
    "georgia o'keeffe": ("Georgia O'Keeffe", "Modernism"),
    "o'keeffe": ("Georgia O'Keeffe", "Modernism"),
    "homer": ("Winslow Homer", "Realism"),
    "winslow homer": ("Winslow Homer", "Realism"),
    "whistler": ("James McNeill Whistler", "Tonalism"),
    "james mcneill whistler": ("James McNeill Whistler", "Tonalism"),
    "norman rockwell": ("Norman Rockwell", "Illustration"),
    "rockwell": ("Norman Rockwell", "Illustration"),
    "sargent": ("John Singer Sargent", "Realism"),
    "john singer sargent": ("John Singer Sargent", "Realism"),
    "mary cassatt": ("Mary Cassatt", "Impressionism"),
    "cassatt": ("Mary Cassatt", "Impressionism"),
    "lichtenstein": ("Roy Lichtenstein", "Pop Art"),
    "roy lichtenstein": ("Roy Lichtenstein", "Pop Art"),
    "jasper johns": ("Jasper Johns", "Pop Art"),
    "robert rauschenberg": ("Robert Rauschenberg", "Neo-Dada"),
    "alexander calder": ("Alexander Calder", "Modernism"),
    "ansel adams": ("Ansel Adams", "Photography"),
    "stuart davis": ("Stuart Davis", "Modernism"),
    "grant wood": ("Grant Wood", "Regionalism"),
    "thomas cole": ("Thomas Cole", "Hudson River School"),
    "frederic church": ("Frederic Edwin Church", "Hudson River School"),
    "albert bierstadt": ("Albert Bierstadt", "Hudson River School"),

    # Scandinavian
    "munch": ("Edvard Munch", "Expressionism"),
    "edvard munch": ("Edvard Munch", "Expressionism"),
    "hammershoi": ("Vilhelm Hammershøi", "Symbolism"),
    "hammershøi": ("Vilhelm Hammershøi", "Symbolism"),

    # Russian
    "malevich": ("Kazimir Malevich", "Suprematism"),
    "kazimir malevich": ("Kazimir Malevich", "Suprematism"),
    "chagall": ("Marc Chagall", "Modernism"),
    "marc chagall": ("Marc Chagall", "Modernism"),
    "repin": ("Ilya Repin", "Realism"),

    # Japanese
    "hokusai": ("Katsushika Hokusai", "Ukiyo-e"),
    "katsushika hokusai": ("Katsushika Hokusai", "Ukiyo-e"),
    "hiroshige": ("Utagawa Hiroshige", "Ukiyo-e"),
    "utagawa hiroshige": ("Utagawa Hiroshige", "Ukiyo-e"),
    "utamaro": ("Kitagawa Utamaro", "Ukiyo-e"),

    # Mexican
    "frida kahlo": ("Frida Kahlo", "Surrealism"),
    "kahlo": ("Frida Kahlo", "Surrealism"),
    "diego rivera": ("Diego Rivera", "Muralism"),
    "rivera": ("Diego Rivera", "Muralism"),
    "orozco": ("José Clemente Orozco", "Muralism"),

    # Chinese
    "ai weiwei": ("Ai Weiwei", "Contemporary"),

    # Other notable
    "mondrian": ("Piet Mondrian", "De Stijl"),
    "piet mondrian": ("Piet Mondrian", "De Stijl"),
    "escher": ("M.C. Escher", "Op Art"),
    "m.c. escher": ("M.C. Escher", "Op Art"),
    "magritte": ("René Magritte", "Surrealism"),
    "rene magritte": ("René Magritte", "Surrealism"),
    "rené magritte": ("René Magritte", "Surrealism"),
    "mucha": ("Alphonse Mucha", "Art Nouveau"),
    "alphonse mucha": ("Alphonse Mucha", "Art Nouveau"),
}


# ---------------------------------------------------------------------------
# Known Genres / Movements Dictionary (~50 entries)
# Keys are lowercase; values are the canonical name
# ---------------------------------------------------------------------------
KNOWN_GENRES = {
    # Major movements
    "impressionism": "Impressionism",
    "impressionist": "Impressionism",
    "post-impressionism": "Post-Impressionism",
    "post impressionism": "Post-Impressionism",
    "expressionism": "Expressionism",
    "expressionist": "Expressionism",
    "abstract expressionism": "Abstract Expressionism",
    "cubism": "Cubism",
    "cubist": "Cubism",
    "surrealism": "Surrealism",
    "surrealist": "Surrealism",
    "realism": "Realism",
    "realist": "Realism",
    "romanticism": "Romanticism",
    "romantic": "Romanticism",
    "baroque": "Baroque",
    "rococo": "Rococo",
    "renaissance": "Renaissance",
    "neoclassicism": "Neoclassicism",
    "neoclassical": "Neoclassicism",
    "modernism": "Modernism",
    "modern art": "Modernism",
    "contemporary": "Contemporary Art",
    "contemporary art": "Contemporary Art",
    "pop art": "Pop Art",
    "minimalism": "Minimalism",
    "minimalist": "Minimalism",
    "abstract": "Abstract Art",
    "abstract art": "Abstract Art",
    "fauvism": "Fauvism",
    "fauvist": "Fauvism",
    "dadaism": "Dadaism",
    "dada": "Dadaism",
    "art nouveau": "Art Nouveau",
    "art deco": "Art Deco",
    "symbolism": "Symbolism",
    "symbolist": "Symbolism",
    "constructivism": "Constructivism",
    "futurism": "Futurism",
    "futurist": "Futurism",
    "pointillism": "Pointillism",
    "mannerism": "Mannerism",
    "gothic": "Gothic Art",
    "gothic art": "Gothic Art",
    "northern renaissance": "Northern Renaissance",
    "dutch golden age": "Dutch Golden Age",
    "hudson river school": "Hudson River School",
    "pre-raphaelite": "Pre-Raphaelite",
    "pre raphaelite": "Pre-Raphaelite",
    "ukiyo-e": "Ukiyo-e",
    "ukiyo e": "Ukiyo-e",
    "japonisme": "Japonisme",
    "suprematism": "Suprematism",
    "de stijl": "De Stijl",
    "neo-expressionism": "Neo-Expressionism",
    "photorealism": "Photorealism",
    "hyperrealism": "Hyperrealism",
    "tonalism": "Tonalism",
    "luminism": "Luminism",
    "street art": "Street Art",
    "op art": "Op Art",
    "conceptual art": "Conceptual Art",

    # Media-based (useful for filtering)
    "watercolor": "Watercolor",
    "watercolour": "Watercolor",
    "oil painting": "Oil Painting",
    "sculpture": "Sculpture",
    "photography": "Photography",
    "printmaking": "Printmaking",
    "etching": "Etching",
    "lithograph": "Lithograph",
    "woodcut": "Woodcut",
    "fresco": "Fresco",
    "pastel": "Pastel",
}


class QueryClassifier:
    """
    Classifies user search queries into structured SearchIntent objects.
    Uses a hybrid approach: local dictionary for known artists/genres,
    Gemini Flash fallback for ambiguous queries.
    """

    def __init__(self):
        self._genai_model = None

    def _get_genai_model(self):
        """Lazy-load the Gemini model only when needed."""
        if self._genai_model is None:
            try:
                import google.generativeai as genai
                api_key = os.getenv("GEMINI_API_KEY")
                if api_key:
                    genai.configure(api_key=api_key)
                    self._genai_model = genai.GenerativeModel('gemini-2.0-flash')
                    logger.info("[Classifier] Gemini Flash model loaded for fallback classification.")
                else:
                    logger.warning("[Classifier] No GEMINI_API_KEY found. AI fallback disabled.")
            except Exception as e:
                logger.warning(f"[Classifier] Failed to load Gemini model: {e}")
        return self._genai_model

    def classify(self, query: str) -> SearchIntent:
        """
        Classify a search query into a structured SearchIntent.

        Priority:
        1. Check KNOWN_ARTISTS dictionary
        2. Check KNOWN_GENRES dictionary
        3. Fall back to Gemini Flash for ambiguous queries
        4. Default to 'freetext' if all else fails
        """
        if not query or not query.strip():
            return SearchIntent(
                query_type="freetext",
                original_query="",
                canonical_name="",
                confidence=1.0
            )

        original = query.strip()
        normalized = original.lower().strip()

        # 1. Check known artists
        if normalized in KNOWN_ARTISTS:
            canonical, era = KNOWN_ARTISTS[normalized]
            logger.info(f"[Classifier] Dictionary match: '{original}' → artist '{canonical}' ({era})")
            return SearchIntent(
                query_type="artist",
                original_query=original,
                canonical_name=canonical,
                era_hint=era,
                confidence=1.0
            )

        # 2. Check known genres
        if normalized in KNOWN_GENRES:
            canonical = KNOWN_GENRES[normalized]
            logger.info(f"[Classifier] Dictionary match: '{original}' → genre '{canonical}'")
            return SearchIntent(
                query_type="genre",
                original_query=original,
                canonical_name=canonical,
                confidence=1.0
            )

        # 3. Gemini Flash fallback for ambiguous queries
        logger.info(f"[Classifier] No dictionary match for '{original}'. Trying AI classification...")
        ai_intent = self._classify_with_ai(original)
        if ai_intent:
            return ai_intent

        # 4. Default to freetext
        logger.info(f"[Classifier] Defaulting to freetext for '{original}'")
        return SearchIntent(
            query_type="freetext",
            original_query=original,
            canonical_name=original,
            confidence=0.3
        )

    def _classify_with_ai(self, query: str) -> Optional[SearchIntent]:
        """Use Gemini Flash to classify an ambiguous query."""
        model = self._get_genai_model()
        if not model:
            return None

        prompt = f"""Classify this art search query for a museum collection search system.

Query: "{query}"

Return ONLY valid JSON (no markdown, no code fences) with these fields:
- "type": one of "artist", "genre", "era", "subject", "medium", "freetext"
- "canonical_name": the standard art-historical name if artist, or the standard term for the genre/era/medium. For subjects, use the query as-is.
- "related_terms": list of 2-3 related search terms that would help find relevant artworks
- "era_hint": the primary art-historical era/movement associated (empty string if not applicable)

Examples:
Query: "starry night" → {{"type": "subject", "canonical_name": "The Starry Night", "related_terms": ["Van Gogh", "night sky", "Post-Impressionism"], "era_hint": "Post-Impressionism"}}
Query: "oil on canvas landscapes" → {{"type": "freetext", "canonical_name": "oil on canvas landscapes", "related_terms": ["landscape painting", "plein air"], "era_hint": ""}}
Query: "art nouveau" → {{"type": "genre", "canonical_name": "Art Nouveau", "related_terms": ["Mucha", "decorative arts", "Jugendstil"], "era_hint": "Art Nouveau"}}
"""
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                if text.startswith("json"):
                    text = text[4:].strip()

            data = json.loads(text)
            intent_type = data.get("type", "freetext")
            # Normalize type to our expected values
            if intent_type in ("era", "medium"):
                intent_type = "genre"  # Treat era and medium like genre for API param purposes

            intent = SearchIntent(
                query_type=intent_type,
                original_query=query,
                canonical_name=data.get("canonical_name", query),
                related_terms=data.get("related_terms", []),
                era_hint=data.get("era_hint", ""),
                confidence=0.8  # AI classification is reliable but not as certain as dictionary
            )
            logger.info(f"[Classifier] AI classified '{query}' → {intent.query_type}: '{intent.canonical_name}'")
            return intent

        except Exception as e:
            logger.warning(f"[Classifier] AI classification failed for '{query}': {e}")
            return None
