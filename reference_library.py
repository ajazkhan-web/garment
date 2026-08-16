"""
reference_library.py — DXF pattern reference library for the apparel pattern bot.

Stores uploaded DXF files with metadata in SQLite. When a new measurement sheet
is uploaded, the bot searches for similar styles and reuses saved geometry.

Uses only Python stdlib: sqlite3, json, hashlib, os, datetime.
"""

import sqlite3
import json
import hashlib
import os
from datetime import datetime


def _loads(text, default):
    """Safe JSON load — returns default on failure."""
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _looks_like_coord(value):
    """Heuristic: skip numeric-only strings that are likely DXF coordinates."""
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


class PatternLibrary:
    """SQLite-backed pattern reference library."""

    def __init__(self, db_path="pattern_references.db"):
        self.db_path = db_path
        self.conn = None
        try:
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self._create_table()
        except sqlite3.Error as e:
            # Degrade gracefully — bot still works without library
            self.conn = None

    def _create_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                garment_type TEXT NOT NULL DEFAULT '',
                silhouette TEXT DEFAULT '',
                has_cowl BOOLEAN DEFAULT 0,
                has_gathers BOOLEAN DEFAULT 0,
                has_pleats BOOLEAN DEFAULT 0,
                pleat_count INTEGER DEFAULT 0,
                asymmetric_hem BOOLEAN DEFAULT 0,
                drop_shoulder BOOLEAN DEFAULT 0,
                has_collar BOOLEAN DEFAULT 0,
                collar_type TEXT DEFAULT '',
                closure TEXT DEFAULT '',
                measurements_json TEXT DEFAULT '{}',
                pieces_json TEXT DEFAULT '[]',
                dxf_content TEXT DEFAULT '',
                style_hash TEXT DEFAULT '',
                file_name TEXT DEFAULT '',
                created_date TEXT DEFAULT '',
                updated_date TEXT DEFAULT ''
            )
        """)
        self.conn.commit()

    # ─── Save ───────────────────────────────────────────────────────

    def save(self, garment_type: str, silhouette: str = "", styling: dict = None,
             measurements: dict = None, pieces: list = None,
             dxf_content: str = "", file_name: str = "") -> int:
        """Save a pattern reference. Returns the row ID, or -1 on failure."""
        if not self.conn:
            return -1
        styling = styling or {}
        measurements = measurements or {}
        pieces = pieces or []
        style_hash = self._compute_style_hash(garment_type, silhouette, styling)
        now = datetime.now().isoformat()
        try:
            cur = self.conn.execute("""
                INSERT INTO pattern_references (
                    garment_type, silhouette, has_cowl, has_gathers, has_pleats,
                    pleat_count, asymmetric_hem, drop_shoulder, has_collar,
                    collar_type, closure, measurements_json, pieces_json,
                    dxf_content, style_hash, file_name, created_date, updated_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                garment_type.lower().strip(),
                silhouette.lower().strip(),
                int(styling.get("has_cowl", False)),
                int(styling.get("has_gathers", False)),
                int(styling.get("has_pleats", False)),
                int(styling.get("pleat_count", 0)),
                int(styling.get("asymmetric_hem", False)),
                int(styling.get("drop_shoulder", False)),
                int(styling.get("has_collar", False)),
                styling.get("collar_type", ""),
                styling.get("closure", ""),
                json.dumps(measurements),
                json.dumps(pieces),
                dxf_content,
                style_hash,
                file_name,
                now, now,
            ))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.Error:
            return -1

    def save_dxf_file(self, file_path: str, garment_type: str = "",
                      silhouette: str = "", measurements: dict = None) -> int:
        """Save an uploaded DXF file to the library.
        Parses basic metadata from DXF text content."""
        if not self.conn:
            return -1
        try:
            with open(file_path, "r", errors="replace") as f:
                dxf_content = f.read()
        except OSError:
            return -1

        # Parse DXF for metadata if not provided
        parsed = self._parse_dxf(dxf_content)
        if not garment_type:
            garment_type = self._infer_garment_type(parsed)
        if not silhouette:
            silhouette = self._infer_silhouette(parsed)
        styling = self._infer_styling(parsed)

        return self.save(
            garment_type=garment_type,
            silhouette=silhouette,
            styling=styling,
            measurements=measurements or {},
            pieces=[],
            dxf_content=dxf_content,
            file_name=os.path.basename(file_path),
        )

    # ─── Search ────────────────────────────────────────────────────

    def search(self, garment_type: str, silhouette: str = "",
               styling: dict = None, limit: int = 5) -> list:
        """Search for similar patterns. Returns list of dicts sorted by score."""
        if not self.conn:
            return []
        styling = styling or {}
        garment_type = garment_type.lower().strip()
        silhouette = silhouette.lower().strip()

        try:
            rows = self.conn.execute(
                "SELECT * FROM pattern_references WHERE garment_type = ?",
                (garment_type,)
            ).fetchall()
        except sqlite3.Error:
            return []

        results = []
        for row in rows:
            score = 0
            # Garment type match = 10 (guaranteed by WHERE clause)
            score += 10
            # Silhouette substring match = 5
            row_sil = (row["silhouette"] or "").lower()
            if silhouette and row_sil:
                if silhouette in row_sil or row_sil in silhouette:
                    score += 5
            # Style feature matches = 2 each
            for key in ("has_cowl", "has_gathers", "has_pleats",
                        "asymmetric_hem", "drop_shoulder", "has_collar"):
                if styling.get(key) and row[key]:
                    score += 2
            # Pleat count overlap = 2
            if styling.get("pleat_count") and row["pleat_count"]:
                if int(styling.get("pleat_count", 0)) == int(row["pleat_count"]):
                    score += 2

            results.append({
                "id": row["id"],
                "garment_type": row["garment_type"],
                "silhouette": row["silhouette"],
                "score": score,
                "pieces": _loads(row["pieces_json"], []),
                "measurements": _loads(row["measurements_json"], {}),
                "dxf_content": row["dxf_content"],
                "file_name": row["file_name"],
                "created_date": row["created_date"],
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def get(self, pattern_id: int) -> dict:
        """Get a specific pattern reference by ID."""
        if not self.conn:
            return None
        try:
            row = self.conn.execute(
                "SELECT * FROM pattern_references WHERE id = ?", (pattern_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "garment_type": row["garment_type"],
                "silhouette": row["silhouette"],
                "measurements": _loads(row["measurements_json"], {}),
                "pieces": _loads(row["pieces_json"], []),
                "dxf_content": row["dxf_content"],
                "file_name": row["file_name"],
                "created_date": row["created_date"],
            }
        except sqlite3.Error:
            return None

    def list_all(self) -> list:
        """List all saved patterns (summary only, no DXF content)."""
        if not self.conn:
            return []
        try:
            rows = self.conn.execute(
                "SELECT id, garment_type, silhouette, file_name, created_date "
                "FROM pattern_references ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def get_reference_pieces(self, garment_type: str, silhouette: str = "",
                             styling: dict = None) -> list:
        """Search for similar patterns and return pieces from the best match.
        Returns None if no good match (score < 10)."""
        results = self.search(garment_type, silhouette, styling, limit=1)
        if not results or results[0]["score"] < 10:
            return None
        pieces = results[0].get("pieces", [])
        if not pieces:
            return None
        return pieces

    # ─── Internal helpers ──────────────────────────────────────────

    def _compute_style_hash(self, garment_type: str, silhouette: str,
                            styling: dict) -> str:
        """Compute a hash of key style features for quick matching."""
        features = [garment_type.lower().strip(), silhouette.lower().strip()]
        if styling:
            for k in ("has_cowl", "has_gathers", "has_pleats",
                      "asymmetric_hem", "drop_shoulder", "has_collar"):
                if styling.get(k):
                    features.append(k)
        return hashlib.md5("|".join(features).encode()).hexdigest()[:12]

    def _parse_dxf(self, content: str) -> dict:
        """Parse DXF text for layer names and TEXT entity strings."""
        lines = content.splitlines()
        layers = set()
        texts = []
        i = 0
        while i < len(lines) - 1:
            code = lines[i].strip()
            value = lines[i + 1].strip()
            if code == "8":
                layers.add(value)
            elif code == "1" and value and not _looks_like_coord(value) and len(value) > 1:
                texts.append(value)
            i += 2
        return {"layers": sorted(layers), "texts": texts}

    def _infer_garment_type(self, parsed: dict) -> str:
        """Infer garment type from DXF layer names and text labels."""
        combined = " ".join(parsed.get("layers", []) + parsed.get("texts", "")).lower()
        for gt in ("dress", "kurti", "blouse", "shirt", "skirt", "gown",
                   "kaftan", "top", "wrap", "bodice", "sleeve", "jacket"):
            if gt in combined:
                return gt
        return "unknown"

    def _infer_silhouette(self, parsed: dict) -> str:
        """Infer silhouette from DXF text labels."""
        combined = " ".join(parsed.get("texts", [])).lower()
        for sil in ("fitted", "a-line", "cowl", "wrap", "asymmetric",
                    "loose", "sheath", "flare", "gathered"):
            if sil in combined:
                return sil
        return ""

    def _infer_styling(self, parsed: dict) -> dict:
        """Infer style features from DXF content."""
        combined = " ".join(parsed.get("layers", []) + parsed.get("texts", [])).lower()
        return {
            "has_cowl": "cowl" in combined,
            "has_gathers": "gather" in combined,
            "has_pleats": "pleat" in combined,
            "pleat_count": combined.count("pleat"),
            "asymmetric_hem": "asymmetric" in combined,
            "drop_shoulder": "drop" in combined,
            "has_collar": "collar" in combined,
            "collar_type": "band" if "band" in combined else "",
            "closure": "zip" if "zip" in combined else ("button" if "button" in combined else ""),
        }

    def close(self):
        """Close the database connection."""
        if self.conn:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None
