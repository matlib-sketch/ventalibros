#!/usr/bin/env python3
"""Genera un PDF con el catálogo completo de la sección Casa."""

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

PRODUCTS = [
    {
        'title': 'Refrigerador Samsung Side by Side 617L',
        'detail': '617 L · Side by Side · No Frost · Inverter · color Silver. Como nuevo (sept. 2020).',
        'price': 567000,
        'original_price': 945000,
        'category': 'Cocina',
        'features': [
            'Dispensador de agua y hielo funcionando perfecto y limpios',
            'Tecnología SpaceMax (más capacidad interior)',
            'Sistema No Frost (no se forma escarcha)',
            'Compresor Digital Inverter (bajo consumo y silencioso)',
            '617 litros totales — 415 L refrigerador / 202 L congelador',
            'Enfría parejo, sin ruidos, muy bien mantenido y limpio',
        ],
        'note': 'Ideal para familias o quienes necesitan harta capacidad. Funciona como nuevo, solo lo vendo por cambio de casa.',
    },
    {
        'title': 'Microondas Grill Somela Reflection 3000 DGM',
        'detail': '30 L · grill 2 en 1 · puerta espejada. Impecable (jul. 2021).',
        'price': 68990,
        'original_price': 114990,
        'category': 'Cocina',
        'features': [
            'Funciona perfecto (microondas 1400W + grill 1100W)',
            '2 en 1: microondas + grill para dorar y gratinar',
            'Incluye rejilla metálica del grill y bandeja de vidrio giratoria',
            'Limpio por dentro y por fuera, sin manchas ni rayas',
            'Panel digital, encendido automático y bloqueo de seguridad para niños',
            '30 litros — espacio para fuentes grandes',
        ],
        'note': 'Anda como nuevo (nuevo cuesta sobre $100.000). Solo lo vendo por cambio de casa.',
    },
    {
        'title': 'Microondas Samsung ME83 — 23 L',
        'detail': '23 L · 700W · puerta espejada. Compacto, ideal depto. 3 años de uso.',
        'price': 43500,
        'original_price': 72500,
        'category': 'Cocina',
        'features': [
            'Funciona perfecto, calienta parejo',
            'Interior de esmalte cerámico (fácil de limpiar, antibacterial)',
            'Incluye bandeja de vidrio giratoria',
            'Limpio por dentro y por fuera',
            'Diseño espejado, compacto — ideal para cocinas chicas, departamentos o pieza',
            '23 litros, 6 niveles de potencia',
        ],
        'note': 'Anda como nuevo. Solo lo vendo por cambio de casa.',
    },
    {
        'title': 'Lavavajillas Fensa Computer 9430 Inox',
        'detail': '14 cubiertos · acero inox · 6 programas. Impecable (jul. 2021).',
        'price': 194990,
        'original_price': 324990,
        'category': 'Cocina',
        'features': [
            'Funciona perfecto: lava y seca sin problemas, sin fugas',
            '14 cubiertos — capacidad grande para toda la familia',
            '6 programas de lavado + panel digital',
            'Tercera bandeja exclusiva para cubiertos (más espacio para vajilla)',
            'Frontal e interior en acero inoxidable',
            'Impecable por dentro y por fuera, con todos los canastos y bandejas',
        ],
        'note': 'Anda como nuevo (nuevo cuesta sobre $280.000). Lo vendo por cambio de casa.',
    },
    {
        'title': 'Hidrolavadora Bauker Fast Plus 1650 PSI',
        'detail': 'Alta presión 1650 PSI · 1400W · con todos los accesorios. Poco uso (1 año).',
        'price': 36000,
        'original_price': 60000,
        'category': 'Herramientas',
        'features': [
            'Funciona perfecto, toma presión sin problemas',
            'Incluye todo: pistola, lanza, manguera de 4 m, boquillas y conector de agua',
            'Ideal para lavar auto, patio, rejas, terrazas y muros',
            'Compacta y liviana, fácil de guardar',
            'Muy poco uso, en excelente estado',
        ],
        'note': 'Como nueva cuesta sobre $50.000. La vendo por cambio de casa.',
    },
    {
        'title': 'Generador Inverter PowerPro XT35iG 3.5 KVA',
        'detail': 'Inverter 3.5 KVA gasolina · onda pura · solo 4 hrs de uso. Casi nuevo.',
        'price': 279000,
        'original_price': 465000,
        'category': 'Herramientas',
        'features': [
            'Solo 4 horas de uso (lo muestra la pantalla digital de horas)',
            'Parte a la primera, sin problemas',
            'Tecnología inverter / onda pura — seguro para notebook, TV, refrigerador y electrónica sensible',
            'Motor 4 tiempos 212cc, estanque 7 L, autonomía 3–4 hrs',
            'Salidas 220V + puertos USB, partida manual',
            'Ideal como respaldo para casa, parcela, taller o eventos',
            'Certificación SEC, conservado impecable',
        ],
        'note': 'Nuevo cuesta sobre $436.000. Lo vendo por cambio de casa.',
    },
]

PRIMARY = HexColor('#2563eb')
DARK = HexColor('#1e293b')
GRAY = HexColor('#64748b')
LIGHT_BG = HexColor('#f1f5f9')
ACCENT = HexColor('#059669')
SALE_RED = HexColor('#dc2626')
BORDER = HexColor('#e2e8f0')


def fmt_price(n):
    s = f"{int(n):,}".replace(",", ".")
    return f"${s}"


def discount_pct(orig, sale):
    return round((1 - sale / orig) * 100)


def build_styles():
    ss = getSampleStyleSheet()

    ss.add(ParagraphStyle(
        'CatalogTitle', parent=ss['Title'],
        fontSize=26, leading=32, textColor=PRIMARY,
        alignment=TA_CENTER, spaceAfter=4,
    ))
    ss.add(ParagraphStyle(
        'CatalogSubtitle', parent=ss['Normal'],
        fontSize=11, leading=14, textColor=GRAY,
        alignment=TA_CENTER, spaceAfter=20,
    ))
    ss.add(ParagraphStyle(
        'ProductTitle', parent=ss['Heading2'],
        fontSize=15, leading=19, textColor=DARK,
        spaceBefore=0, spaceAfter=2,
    ))
    ss.add(ParagraphStyle(
        'ProductDetail', parent=ss['Normal'],
        fontSize=10, leading=13, textColor=GRAY,
        spaceAfter=6,
    ))
    ss.add(ParagraphStyle(
        'Feature', parent=ss['Normal'],
        fontSize=9.5, leading=13, textColor=DARK,
        leftIndent=12, bulletIndent=0,
    ))
    ss.add(ParagraphStyle(
        'ProductNote', parent=ss['Normal'],
        fontSize=9.5, leading=13, textColor=GRAY,
        fontName='Helvetica-Oblique', spaceBefore=6, spaceAfter=4,
    ))
    ss.add(ParagraphStyle(
        'PriceOriginal', parent=ss['Normal'],
        fontSize=10, leading=13, textColor=GRAY,
    ))
    ss.add(ParagraphStyle(
        'PriceSale', parent=ss['Normal'],
        fontSize=14, leading=18, textColor=SALE_RED,
        fontName='Helvetica-Bold',
    ))
    ss.add(ParagraphStyle(
        'Badge', parent=ss['Normal'],
        fontSize=9, leading=12, textColor=ACCENT,
        fontName='Helvetica-Bold',
    ))
    ss.add(ParagraphStyle(
        'Footer', parent=ss['Normal'],
        fontSize=8, leading=10, textColor=GRAY,
        alignment=TA_CENTER,
    ))
    ss.add(ParagraphStyle(
        'CategoryHeader', parent=ss['Heading3'],
        fontSize=13, leading=16, textColor=PRIMARY,
        spaceBefore=14, spaceAfter=8,
        fontName='Helvetica-Bold',
    ))
    return ss


def product_block(p, styles):
    elements = []

    cat_label = p['category'].upper()
    disc = discount_pct(p['original_price'], p['price'])

    title_text = f"{p['title']}"
    elements.append(Paragraph(title_text, styles['ProductTitle']))
    elements.append(Paragraph(p['detail'], styles['ProductDetail']))

    for feat in p['features']:
        elements.append(Paragraph(f"&#x2713;  {feat}", styles['Feature']))

    elements.append(Paragraph(p['note'], styles['ProductNote']))

    elements.append(Spacer(1, 4 * mm))

    price_line = (
        f'<font color="{GRAY.hexval()}"><strike>{fmt_price(p["original_price"])}</strike></font>'
        f'&nbsp;&nbsp;&nbsp;'
        f'<font color="{SALE_RED.hexval()}" size="14"><b>{fmt_price(p["price"])}</b></font>'
        f'&nbsp;&nbsp;'
        f'<font color="{ACCENT.hexval()}" size="9"><b>-{disc}% dcto.</b></font>'
    )
    elements.append(Paragraph(price_line, styles['Normal']))

    elements.append(Spacer(1, 3 * mm))
    elements.append(HRFlowable(
        width="100%", thickness=0.5, color=BORDER,
        spaceBefore=2, spaceAfter=8
    ))
    return elements


def build_pdf(filename='catalogo_casa.pdf'):
    styles = build_styles()

    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
    )

    story = []

    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("Catalogo Casa", styles['CatalogTitle']))
    story.append(Paragraph(
        "Articulos para el hogar &amp; herramientas — Santiago, Chile",
        styles['CatalogSubtitle']
    ))
    story.append(Paragraph(
        "Todos los precios son conversables. Retiro en Santiago.",
        styles['CatalogSubtitle']
    ))
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(
        width="100%", thickness=1.5, color=PRIMARY,
        spaceBefore=0, spaceAfter=12
    ))

    current_cat = None
    for p in PRODUCTS:
        if p['category'] != current_cat:
            current_cat = p['category']
            icon = "&#x1F373;" if current_cat == 'Cocina' else "&#x1F527;"
            story.append(Paragraph(
                f"{current_cat.upper()}",
                styles['CategoryHeader']
            ))
            story.append(HRFlowable(
                width="100%", thickness=0.5, color=PRIMARY,
                spaceBefore=0, spaceAfter=8
            ))

        story.extend(product_block(p, styles))

    story.append(Spacer(1, 1 * cm))
    story.append(HRFlowable(
        width="100%", thickness=1, color=PRIMARY,
        spaceBefore=0, spaceAfter=8
    ))
    story.append(Paragraph(
        "Contacto: matis.lib@gmail.com &nbsp;|&nbsp; WhatsApp: +56 9 9742 3516",
        styles['Footer']
    ))
    story.append(Paragraph(
        "Todos los articulos se entregan en Santiago. Precios conversables.",
        styles['Footer']
    ))

    doc.build(story)
    print(f"PDF generado: {filename}")


if __name__ == '__main__':
    build_pdf()
