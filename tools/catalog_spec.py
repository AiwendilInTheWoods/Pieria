"""
Curation spec for the offline catalog builder.

Each collection is hand-curated: a theme, the sources to pull from, the queries that surface "the
good stuff", and a target count. The builder (tools/build_catalog.py) fetches candidates per source,
ranks (museum highlights first) + dedups, takes the top `target`, enriches a placard, verifies the
image URLs, and writes static/catalog/<id>.json + an index entry.

Sources:
  - museum source ids (keyless, PD/CC0): "chicago", "met", "cleveland", "rijks", "smk"
  - "nasa"      → images.nasa.gov (public domain)
  - "loc"       → Library of Congress (public-domain posters & documentary photography)
  - "wikimedia" → specific curated PD files (use `files=[...]` of Commons filenames)

Modern rock/movie/band posters are copyrighted and intentionally excluded; the poster collection is
vintage public-domain only (Art Nouveau, WPA, travel, propaganda).
"""

COLLECTIONS = [
    {
        "id": "impressionism",
        "title": "Impressionism",
        "description": "Light, color, and fleeting moments from the movement that broke with the academy.",
        "license": "Public Domain",
        "sources": ["chicago", "met", "cleveland"],
        "queries": ["Claude Monet", "Pierre-Auguste Renoir", "Edgar Degas", "Camille Pissarro", "Berthe Morisot"],
        "target": 24,
    },
    {
        "id": "post-impressionism",
        "title": "Post-Impressionism",
        "description": "Bold color and expressive form — the bridge from Impressionism to modern art.",
        "license": "Public Domain",
        "sources": ["chicago", "met", "rijks"],
        "queries": ["Vincent van Gogh", "Paul Cezanne", "Paul Gauguin", "Georges Seurat", "Henri de Toulouse-Lautrec"],
        "target": 24,
    },
    {
        "id": "dutch-golden-age",
        "title": "The Dutch Golden Age",
        "description": "Light, intimacy, and virtuosity from the 17th-century Netherlands.",
        "license": "Public Domain",
        "sources": ["rijks", "met"],
        "queries": ["Rembrandt van Rijn", "Johannes Vermeer", "Frans Hals", "Jan Steen", "Pieter de Hooch"],
        "target": 20,
    },
    {
        "id": "renaissance",
        "title": "The Italian Renaissance",
        "description": "Harmony, perspective, and humanism from the rebirth of classical ideals.",
        "license": "Public Domain",
        "sources": ["met", "chicago"],
        "queries": ["Sandro Botticelli", "Raphael", "Titian", "Leonardo da Vinci", "Renaissance Italian painting"],
        "target": 18,
    },
    {
        "id": "ukiyo-e",
        "title": "Japanese Ukiyo-e",
        "description": "Woodblock prints of the 'floating world' — landscapes, kabuki, and everyday Edo life.",
        "license": "Public Domain",
        "sources": ["chicago", "met", "smk"],
        "queries": ["Katsushika Hokusai", "Utagawa Hiroshige", "Kitagawa Utamaro", "ukiyo-e woodblock print"],
        "target": 24,
    },
    {
        "id": "romanticism",
        "title": "Romanticism & the Sublime",
        "description": "Nature at its most awe-inspiring, and the human drama set against it.",
        "license": "Public Domain",
        "sources": ["met", "chicago", "cleveland"],
        "queries": ["Caspar David Friedrich", "J. M. W. Turner", "Eugene Delacroix", "Thomas Cole", "Romantic landscape"],
        "target": 18,
    },
    {
        "id": "american-art",
        "title": "American Art",
        "description": "From frontier light to mid-century city scenes — the American eye.",
        "license": "Public Domain",
        "sources": ["chicago", "met", "cleveland"],
        "queries": ["Edward Hopper", "Winslow Homer", "John Singer Sargent", "Grant Wood", "James McNeill Whistler"],
        "target": 20,
    },
    {
        "id": "portraits",
        "title": "Portraits Through Time",
        "description": "Faces that have held our gaze across the centuries.",
        "license": "Public Domain",
        "sources": ["met", "rijks", "chicago"],
        "queries": ["portrait painting", "self-portrait", "portrait of a woman", "portrait of a man"],
        "target": 20,
    },
    {
        "id": "still-life",
        "title": "Still Life & Flowers",
        "description": "Quiet arrangements of fruit, flowers, and objects — beauty in the everyday.",
        "license": "Public Domain",
        "sources": ["rijks", "met", "chicago"],
        "queries": ["still life flowers", "vanitas still life", "floral still life", "fruit still life"],
        "target": 18,
    },
    {
        "id": "sculpture-antiquity",
        "title": "Sculpture & Antiquity",
        "description": "Marble, bronze, and stone — the enduring forms of the ancient and classical world.",
        "license": "Public Domain",
        "sources": ["met", "cleveland"],
        "queries": ["Greek sculpture", "Roman marble statue", "ancient Egyptian art", "classical bronze sculpture"],
        "target": 18,
    },
    {
        "id": "botanical",
        "title": "Botanical & Natural History",
        "description": "The art of scientific observation — birds, plants, and the natural world in fine detail.",
        "license": "Public Domain",
        "sources": ["met", "chicago", "smk"],
        "queries": ["John James Audubon birds", "botanical illustration", "natural history print"],
        "target": 16,
    },
    {
        "id": "cosmos",
        "title": "The Cosmos",
        "description": "Public-domain views of deep space from Hubble and the James Webb Space Telescope.",
        "license": "Public Domain (NASA)",
        "sources": ["nasa"],
        "queries": ["Hubble nebula", "James Webb deep field", "galaxy Hubble", "star cluster Hubble"],
        "target": 18,
    },
    {
        "id": "earth-and-spaceflight",
        "title": "Earth & Spaceflight",
        "description": "Our planet and the missions that let us see it — from Apollo to the Space Station.",
        "license": "Public Domain (NASA)",
        "sources": ["nasa"],
        "queries": ["Apollo Earth", "Blue Marble Earth", "Earth from space", "astronaut spacewalk"],
        "target": 14,
    },
    {
        "id": "vintage-posters",
        "title": "Vintage Posters & Graphic Design",
        "description": "Public-domain poster art — Art Nouveau, travel, and WPA design at its most striking.",
        "license": "Public Domain",
        "sources": ["loc", "wikimedia"],
        "queries": ["WPA poster", "travel poster", "art nouveau poster"],
        "files": [
            "Alphonse Mucha - 1896 - Salon des Cent.jpg",
            "Mucha-Job-1896.jpg",
            "Reverie (Alphonse Mucha).jpg",
            "Toulouse-Lautrec - Moulin Rouge - La Goulue.jpg",
            "Eugène Grasset - Grafton Galleries.jpg",
        ],
        "target": 18,
    },
    {
        "id": "documentary-photography",
        "title": "Documentary Photography",
        "description": "The unflinching American photograph — the Depression-era FSA archive and beyond.",
        "license": "Public Domain (Library of Congress)",
        "sources": ["loc"],
        "queries": ["Dorothea Lange", "Farm Security Administration photograph", "Walker Evans", "Russell Lee photograph"],
        "target": 16,
    },
    {
        "id": "modern-masters",
        "title": "Modern Masters",
        "description": "The turn into modernism — symbolism, expression, and the new century (public-domain works).",
        "license": "Public Domain",
        "sources": ["met", "chicago", "smk"],
        "queries": ["Gustav Klimt", "Edvard Munch", "Henri Matisse", "Wassily Kandinsky"],
        "target": 16,
    },
]


def get_collection(collection_id: str):
    return next((c for c in COLLECTIONS if c["id"] == collection_id), None)
