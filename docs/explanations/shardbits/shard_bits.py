from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import Flowable
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Group
from reportlab.graphics import renderPDF

# ── Palette ────────────────────────────────────────────────────────────────────
C_BG        = colors.HexColor("#0D1117")   # page background
C_SURFACE   = colors.HexColor("#161B22")   # card / section bg
C_BORDER    = colors.HexColor("#30363D")   # border / rule
C_BLUE      = colors.HexColor("#378ADD")   # accent blue
C_BLUE_DIM  = colors.HexColor("#0C2D47")   # shard bit cell bg
C_BLUE_TXT  = colors.HexColor("#85B7EB")   # shard bit text
C_AMBER     = colors.HexColor("#EF9F27")   # highlight / used bytes
C_AMBER_DIM = colors.HexColor("#2A1E06")   # amber cell bg
C_TEXT      = colors.HexColor("#E6EDF3")   # primary text
C_MUTED     = colors.HexColor("#8B949E")   # secondary text
C_DISCARD   = colors.HexColor("#21262D")   # discarded bit cell
C_CODE_BG   = colors.HexColor("#0D1117")   # inline code bg
C_GREEN     = colors.HexColor("#3FB950")   # result value
C_GREEN_DIM = colors.HexColor("#0D2A14")

W, H = letter

# ── Styles ─────────────────────────────────────────────────────────────────────
def make_styles():
    return {
        "title": ParagraphStyle("title",
            fontName="Helvetica-Bold", fontSize=22, textColor=C_TEXT,
            spaceAfter=4, leading=28),
        "subtitle": ParagraphStyle("subtitle",
            fontName="Helvetica", fontSize=12, textColor=C_MUTED,
            spaceAfter=18, leading=16),
        "h2": ParagraphStyle("h2",
            fontName="Helvetica-Bold", fontSize=13, textColor=C_BLUE,
            spaceBefore=18, spaceAfter=8, leading=18),
        "step": ParagraphStyle("step",
            fontName="Helvetica-Bold", fontSize=10, textColor=C_AMBER,
            spaceBefore=2, spaceAfter=4, leading=14),
        "body": ParagraphStyle("body",
            fontName="Helvetica", fontSize=10, textColor=C_TEXT,
            spaceAfter=8, leading=15),
        "muted": ParagraphStyle("muted",
            fontName="Helvetica", fontSize=9, textColor=C_MUTED,
            spaceAfter=6, leading=13),
        "code": ParagraphStyle("code",
            fontName="Courier-Bold", fontSize=11, textColor=C_AMBER,
            backColor=C_SURFACE, spaceAfter=8, leading=16,
            leftIndent=10, rightIndent=10,
            borderPad=8),
        "code_body": ParagraphStyle("code_body",
            fontName="Courier", fontSize=9, textColor=C_TEXT,
            backColor=C_CODE_BG, spaceAfter=0, leading=14,
            leftIndent=12),
        "caption": ParagraphStyle("caption",
            fontName="Helvetica", fontSize=8, textColor=C_MUTED,
            spaceAfter=4, leading=11, alignment=TA_CENTER),
        "result_label": ParagraphStyle("result_label",
            fontName="Helvetica", fontSize=9, textColor=C_MUTED,
            spaceAfter=2, leading=12),
        "result_val": ParagraphStyle("result_val",
            fontName="Courier-Bold", fontSize=16, textColor=C_GREEN,
            spaceAfter=4, leading=20),
        "endian_title": ParagraphStyle("endian_title",
            fontName="Helvetica-Bold", fontSize=11, textColor=C_TEXT,
            spaceAfter=6, leading=14),
        "footer": ParagraphStyle("footer",
            fontName="Helvetica", fontSize=8, textColor=C_MUTED,
            alignment=TA_CENTER, leading=11),
    }

S = make_styles()

# ── Dark page background ───────────────────────────────────────────────────────
class DarkBackground(Flowable):
    def draw(self):
        pass

def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    # Subtle top accent line
    canvas.setFillColor(C_BLUE)
    canvas.rect(0, H - 3, W, 3, fill=1, stroke=0)
    # Footer
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(C_MUTED)
    canvas.drawCentredString(W/2, 0.4*inch, "Shard Bit Extraction — BSV Multicast Infrastructure")
    canvas.drawRightString(W - 0.75*inch, 0.4*inch, f"Page {doc.page}")
    canvas.restoreState()

# ── Bit row graphic ────────────────────────────────────────────────────────────
class BitRow(Flowable):
    """Draws a 32-bit row with shard bits highlighted, then discarded."""
    def __init__(self, uint32_val, shard_bits=12, width=460, label=None):
        super().__init__()
        self.val = uint32_val
        self.shard_bits = shard_bits
        self.row_width = width
        self.label = label
        self._bw = (width - 31) / 32   # bit cell width

    def wrap(self, aw, ah):
        return self.row_width, 38 + (14 if self.label else 0)

    def draw(self):
        bw = self._bw
        y_offset = 14 if self.label else 0

        if self.label:
            self.canv.setFont("Helvetica", 8)
            self.canv.setFillColor(C_MUTED)
            self.canv.drawString(0, 2, self.label)

        bits = [(self.val >> (31 - i)) & 1 for i in range(32)]
        for i, b in enumerate(bits):
            x = i * (bw + 1)
            is_shard = i < self.shard_bits
            bg = C_BLUE_DIM if is_shard else C_DISCARD
            border = C_BLUE if is_shard else C_BORDER
            txt_color = C_BLUE_TXT if is_shard else C_MUTED

            self.canv.setFillColor(bg)
            self.canv.setStrokeColor(border)
            self.canv.setLineWidth(0.5)
            self.canv.roundRect(x, y_offset + 10, bw, 22, 2, fill=1, stroke=1)

            self.canv.setFillColor(txt_color)
            self.canv.setFont("Courier-Bold" if is_shard else "Courier", 8)
            self.canv.drawCentredString(x + bw/2, y_offset + 16, str(b))

        # Zone labels below
        shard_w = self.shard_bits * (bw + 1) - 1
        discard_w = (32 - self.shard_bits) * (bw + 1) - 1
        self.canv.setFont("Helvetica", 7)
        self.canv.setFillColor(C_BLUE_TXT)
        self.canv.drawCentredString(shard_w/2, y_offset + 3, f"← {self.shard_bits} shard bits →")
        self.canv.setFillColor(C_MUTED)
        self.canv.drawCentredString(shard_w + 1 + discard_w/2, y_offset + 3,
                                    f"← {32 - self.shard_bits} discarded bits →")

# ── Hex byte row graphic ───────────────────────────────────────────────────────
class HexByteRow(Flowable):
    def __init__(self, byte_vals, used=4, width=320):
        super().__init__()
        self.bytes = byte_vals
        self.used = used
        self.row_width = width

    def wrap(self, aw, ah):
        return self.row_width, 30

    def draw(self):
        n = len(self.bytes)
        bw = 34
        gap = 4
        for i, b in enumerate(self.bytes):
            x = i * (bw + gap)
            is_used = i < self.used
            bg = C_AMBER_DIM if is_used else C_DISCARD
            border = C_AMBER if is_used else C_BORDER

            self.canv.setFillColor(bg)
            self.canv.setStrokeColor(border)
            self.canv.setLineWidth(0.5 if not is_used else 1)
            self.canv.roundRect(x, 6, bw, 20, 3, fill=1, stroke=1)

            self.canv.setFillColor(C_AMBER if is_used else C_MUTED)
            self.canv.setFont("Courier-Bold" if is_used else "Courier", 9)
            self.canv.drawCentredString(x + bw/2, 12, f"{b:02X}")

        # Dots
        x_dots = n * (bw + gap)
        self.canv.setFillColor(C_MUTED)
        self.canv.setFont("Helvetica", 10)
        self.canv.drawString(x_dots, 12, " …")

# ── Sample TXID data ───────────────────────────────────────────────────────────
SAMPLES = [
    ("a3f7c2e1...", [0xa3, 0xf7, 0xc2, 0xe1, 0xb9, 0xd0, 0x45, 0x12]),
    ("000000001a2b...", [0x00, 0x00, 0x00, 0x00, 0x1a, 0x2b, 0x3c, 0x4d]),
    ("ffffffff0000...", [0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00]),
    ("deadbeef1234...", [0xde, 0xad, 0xbe, 0xef, 0x12, 0x34, 0x56, 0x78]),
]

def be_uint32(b): return (b[0] << 24 | b[1] << 16 | b[2] << 8 | b[3]) & 0xFFFFFFFF
def le_uint32(b): return (b[3] << 24 | b[2] << 16 | b[1] << 8 | b[0]) & 0xFFFFFFFF

# ── Divider ────────────────────────────────────────────────────────────────────
def rule():
    return HRFlowable(width="100%", thickness=0.5, color=C_BORDER, spaceAfter=10, spaceBefore=4)

def card_table(content_rows, col_widths=None):
    """Wrap rows in a dark surface card."""
    t = Table(content_rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_SURFACE),
        ("BOX", (0,0), (-1,-1), 0.5, C_BORDER),
        ("ROUNDEDCORNERS", [6]),
        ("TOPPADDING", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("LEFTPADDING", (0,0), (-1,-1), 14),
        ("RIGHTPADDING", (0,0), (-1,-1), 14),
    ]))
    return t

# ── Build the PDF ──────────────────────────────────────────────────────────────
out_path = "/mnt/user-data/outputs/shard_bit_extraction.pdf"
doc = SimpleDocTemplate(
    out_path,
    pagesize=letter,
    leftMargin=0.75*inch, rightMargin=0.75*inch,
    topMargin=0.85*inch, bottomMargin=0.75*inch,
    title="Shard Bit Extraction — BSV Multicast Infrastructure",
    author="Lightweb Inc.",
)

story = []

# ── Title block ────────────────────────────────────────────────────────────────
story.append(Spacer(1, 0.1*inch))
story.append(Paragraph("Shard Bit Extraction", S["title"]))
story.append(Paragraph(
    "How <font color='#EF9F27'><b>shardBits = 12</b></font> maps a TXID to one of 4096 shards "
    "using big-endian uint32 and a right-shift", S["subtitle"]))
story.append(rule())

# ── ELI5 ──────────────────────────────────────────────────────────────────────
story.append(Paragraph("ELI5 — Explain It Like I'm 5", S["h2"]))

eli5_visual_rows = [[
    Paragraph("<font color='#8B949E'>TXID bytes</font>", S["muted"]),
    Paragraph("<font color='#EF9F27' name='Courier-Bold'>[A3] [F7] [C2] [E1]</font>  ···", S["code_body"]),
],[
    Paragraph("<font color='#8B949E'>Read as uint32</font>", S["muted"]),
    Paragraph("<font color='#EF9F27' name='Courier-Bold'>0xA3F7C2E1</font>", S["code_body"]),
],[
    Paragraph("<font color='#8B949E'>Top 12 bits</font>", S["muted"]),
    Paragraph("<font color='#3FB950' name='Courier-Bold'>0xA3F  =  2623</font>  ← shard #2623 of 4096", S["code_body"]),
]]

t_visual = Table(eli5_visual_rows, colWidths=[100, 340])
t_visual.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,-1), C_CODE_BG),
    ("BOX", (0,0), (-1,-1), 0.5, C_BORDER),
    ("INNERGRID", (0,0), (-1,-1), 0.3, C_BORDER),
    ("TOPPADDING", (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING", (0,0), (-1,-1), 10),
    ("RIGHTPADDING", (0,0), (-1,-1), 10),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    # Highlight the result row
    ("BACKGROUND", (0,2), (-1,2), C_GREEN_DIM),
    ("BOX", (0,2), (-1,2), 0.5, C_GREEN),
]))

eli5_outer = Table([
    [Paragraph(
        "<b><font color='#EF9F27'>Imagine a post office with 4096 sorting bins.</font></b>",
        ParagraphStyle("eli5h", fontName="Helvetica-Bold", fontSize=11,
                       textColor=C_AMBER, spaceAfter=8, leading=16))],
    [Paragraph(
        "Every Bitcoin transaction has an ID — a long random-looking number (the TXID). "
        "We want to quickly decide which of the 4096 bins it belongs in, "
        "without doing any expensive math.",
        S["body"])],
    [Paragraph(
        "<b>Here's the trick:</b> look at just the <i>first 4 bytes</i> of the TXID, "
        "line them up in order (that's the big-endian part), then "
        "grab only the <i>first 12 bits</i> of those bytes (that's the right-shift). "
        "Those 12 bits give a number from 0 to 4095 -- the bin number.",
        S["body"])],
    [Spacer(1, 6)],
    [t_visual],
    [Spacer(1, 6)],
    [Paragraph(
        "Because TXIDs are random hashes, every bin gets roughly the same number of "
        "transactions. <b>Fast, fair, and no division required.</b>",
        S["body"])],
], colWidths=[460])
eli5_outer.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,-1), C_SURFACE),
    ("BOX", (0,0), (-1,-1), 1, C_AMBER),
    ("ROUNDEDCORNERS", [6]),
    ("TOPPADDING", (0,0), (-1,-1), 10),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ("LEFTPADDING", (0,0), (-1,-1), 14),
    ("RIGHTPADDING", (0,0), (-1,-1), 14),
    # Amber left accent bar effect via top border
    ("LINEABOVE", (0,0), (-1,0), 3, C_AMBER),
]))
story.append(eli5_outer)
story.append(rule())

# ── The code ───────────────────────────────────────────────────────────────────
story.append(Paragraph("Source", S["h2"]))
code_data = [
    [Paragraph("<font color='#85B7EB'>shardBits</font>  = 12", S["code_body"])],
    [Paragraph(
        "<font color='#85B7EB'>groupIndex</font> = binary.BigEndian.Uint32(txid[0:4]) &gt;&gt; (32 - shardBits)",
        S["code_body"])],
]
story.append(card_table(code_data, col_widths=[460]))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "This extracts the top 12 bits of the big-endian uint32 formed by the first 4 bytes "
    "of the TXID, producing a shard index in the range 0–4095.", S["body"]))

story.append(rule())

# ── Step-by-step for first sample ─────────────────────────────────────────────
story.append(Paragraph("Step-by-Step Walkthrough", S["h2"]))

for label, b in SAMPLES[:1]:
    be = be_uint32(b)
    group = be >> 20

    story.append(Paragraph(f"Sample TXID: <font name='Courier-Bold' color='#EF9F27'>{label}</font>",
                            S["step"]))
    story.append(Spacer(1, 4))

    story.append(Paragraph("Step 1 — Take first 4 bytes (highlighted)", S["muted"]))
    story.append(HexByteRow(b, used=4, width=320))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        f"Step 2 — Assemble as big-endian uint32: "
        f"<font name='Courier-Bold' color='#EF9F27'>0x{b[0]:02X}{b[1]:02X}{b[2]:02X}{b[3]:02X}"
        f" = {be:,}</font>", S["muted"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Step 3 — View all 32 bits; top 12 are the shard index", S["muted"]))
    story.append(BitRow(be, shard_bits=12, width=460))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        f"Step 4 — Right-shift by 20 (= 32 − 12), keeping top 12 bits:", S["muted"]))
    story.append(Spacer(1, 4))
    result_data = [[
        Paragraph("groupIndex", S["result_label"]),
        Paragraph(f"{group}", S["result_val"]),
        Paragraph(f"0x{group:03X}", S["result_val"]),
        Paragraph(f"shard {group} of 4096", S["muted"]),
    ]]
    t = Table(result_data, colWidths=[80, 60, 60, 160])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), C_GREEN_DIM),
        ("BOX", (0,0), (-1,-1), 1, C_GREEN),
        ("ROUNDEDCORNERS", [6]),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 12),
        ("RIGHTPADDING", (0,0), (-1,-1), 12),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(t)

story.append(rule())

# ── Big-endian vs Little-endian comparison ─────────────────────────────────────
story.append(Paragraph("Big-Endian vs Little-Endian Comparison", S["h2"]))
story.append(Paragraph(
    "The endianness of the Uint32 read changes <i>which</i> bytes of the TXID contribute "
    "to the shard index. Big-endian places <font name='Courier-Bold'>txid[0]</font> as the "
    "most significant byte — so it drives the shard bits. "
    "Little-endian would put <font name='Courier-Bold'>txid[3]</font> there instead.",
    S["body"]))

# Table header row
hdr = [
    Paragraph("TXID prefix", S["muted"]),
    Paragraph("Encoding", S["muted"]),
    Paragraph("uint32 (hex)", S["muted"]),
    Paragraph("groupIndex", S["muted"]),
    Paragraph("shard of 4096", S["muted"]),
]
rows = [hdr]

for label, b in SAMPLES:
    be = be_uint32(b)
    le = le_uint32(b)
    be_g = be >> 20
    le_g = le >> 20
    rows.append([
        Paragraph(f"<font name='Courier'>{label}</font>", S["muted"]),
        Paragraph("<font color='#85B7EB'><b>BigEndian</b></font>", S["code_body"]),
        Paragraph(f"<font name='Courier-Bold' color='#EF9F27'>0x{be:08X}</font>", S["muted"]),
        Paragraph(f"<font name='Courier-Bold' color='#3FB950'>{be_g}</font>", S["muted"]),
        Paragraph(f"<font name='Courier'>{be_g}/4095</font>", S["muted"]),
    ])
    rows.append([
        Paragraph("", S["muted"]),
        Paragraph("<font color='#8B949E'>LittleEndian</font>", S["code_body"]),
        Paragraph(f"<font name='Courier'>0x{le:08X}</font>", S["muted"]),
        Paragraph(f"<font name='Courier'>{le_g}</font>", S["muted"]),
        Paragraph(f"<font name='Courier'>{le_g}/4095</font>", S["muted"]),
    ])

cw = [110, 85, 90, 75, 80]
t = Table(rows, colWidths=cw, repeatRows=1)
t.setStyle(TableStyle([
    # Header
    ("BACKGROUND", (0,0), (-1,0), C_BORDER),
    ("TEXTCOLOR", (0,0), (-1,0), C_MUTED),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,0), 8),
    # Body
    ("BACKGROUND", (0,1), (-1,-1), C_SURFACE),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [C_SURFACE, colors.HexColor("#1A2030")]),
    ("BOX", (0,0), (-1,-1), 0.5, C_BORDER),
    ("INNERGRID", (0,0), (-1,-1), 0.3, C_BORDER),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
    ("RIGHTPADDING", (0,0), (-1,-1), 8),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
]))
story.append(t)

story.append(rule())

# ── Bit anatomy for all 4 samples ─────────────────────────────────────────────
story.append(Paragraph("Bit-Level View — All Samples", S["h2"]))
story.append(Paragraph(
    "Each row shows all 32 bits of the big-endian uint32. "
    "<font color='#85B7EB'>Blue</font> = shard index bits (top 12). "
    "Gray = discarded (bottom 20).", S["body"]))

for label, b in SAMPLES:
    be = be_uint32(b)
    group = be >> 20
    story.append(KeepTogether([
        BitRow(be, shard_bits=12, width=460,
               label=f"{label}   →  groupIndex = {group}  (0x{group:03X})"),
        Spacer(1, 6),
    ]))

story.append(rule())

# ── Why it works ───────────────────────────────────────────────────────────────
story.append(Paragraph("Why This Is a Good Sharding Function", S["h2"]))

bullets = [
    ("<b>Uniform distribution.</b>",
     "TXIDs are double-SHA256 hashes, so their bytes are effectively uniform random. "
     "The top 12 bits will land uniformly across all 4096 shards — no hot spots."),
    ("<b>Zero modulo cost.</b>",
     "A right-shift is a single CPU instruction. Compared to a modulo operation "
     "(integer division), this is ~5–10x faster at high throughput."),
    ("<b>Deterministic and stateless.</b>",
     "Any node seeing the same TXID independently computes the same shard index — "
     "no coordination required. Critical for multicast fan-out."),
    ("<b>Endianness is load-bearing.</b>",
     "BigEndian ensures txid[0] (the first wire byte) drives the shard bits. "
     "A LittleEndian read would use txid[3] instead, producing a completely different — "
     "and potentially skewed — distribution if TXIDs had any byte-positional bias."),
    ("<b>Tunable range.</b>",
     "Changing shardBits changes the shard count: 10 bits → 1024 shards, "
     "12 bits → 4096 shards, 14 bits → 16384 shards. Only the shift constant changes."),
]

for bold, text in bullets:
    story.append(Paragraph(f"{bold} {text}", S["body"]))

story.append(Spacer(1, 6))

# ── Shard count reference table ────────────────────────────────────────────────
story.append(Paragraph("Shard count by shardBits value", S["step"]))
ref_rows = [
    [Paragraph(h, S["muted"]) for h in ["shardBits", "Shards (2^n)", "Index range", "Right-shift"]],
]
for bits in [8, 10, 12, 14, 16]:
    shards = 2**bits
    ref_rows.append([
        Paragraph(f"<font name='Courier-Bold' color='#EF9F27'>{bits}</font>", S["muted"]),
        Paragraph(f"<font name='Courier'>{shards:,}</font>", S["muted"]),
        Paragraph(f"<font name='Courier'>0 – {shards-1:,}</font>", S["muted"]),
        Paragraph(f"<font name='Courier'>&gt;&gt; {32 - bits}</font>", S["muted"]),
    ])

t = Table(ref_rows, colWidths=[80, 100, 110, 90])
t.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), C_BORDER),
    ("BACKGROUND", (0,1), (-1,-1), C_SURFACE),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [C_SURFACE, colors.HexColor("#1A2030")]),
    # Highlight the shardBits=12 row
    ("BACKGROUND", (0,3), (-1,3), C_BLUE_DIM),
    ("BOX", (0,0), (-1,-1), 0.5, C_BORDER),
    ("INNERGRID", (0,0), (-1,-1), 0.3, C_BORDER),
    ("TOPPADDING", (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING", (0,0), (-1,-1), 10),
    ("RIGHTPADDING", (0,0), (-1,-1), 10),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,0), 8),
]))
story.append(t)
story.append(Paragraph("Row highlighted in blue = current config (shardBits = 12)", S["caption"]))

story.append(Spacer(1, 0.1*inch))
story.append(rule())
story.append(Paragraph("Lightweb Inc. · BSV Multicast Infrastructure · lightwebinc", S["footer"]))

# ── Build ──────────────────────────────────────────────────────────────────────
doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print(f"PDF written to {out_path}")
