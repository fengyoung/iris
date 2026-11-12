from pathlib import Path
import copy, hashlib, io, json, sys
import xml.etree.ElementTree as ET
sys.path.insert(0, '/tmp/iris-caption-logo-render-v1')
import ctypes.util
_original_find_library = ctypes.util.find_library
ctypes.util.find_library = lambda name: '/opt/homebrew/Cellar/cairo/1.18.4/lib/libcairo.2.dylib' if name in ('cairo', 'cairo-2', 'libcairo-2') else _original_find_library(name)
import cairosvg
from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/iris-logo'
PREFIX='2026-11-05_iris-caption-logo_v1.0_'
SOURCE=ROOT/'apps/iris-caption/design/logo/iris-caption-B.svg'
REF=OUT/'2026-11-05_iris-logo_v1.4_lockup-primary.svg'
FONT=Path('/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf')
NS='{http://www.w3.org/2000/svg}'
approved=SOURCE.read_text()
ref=ET.fromstring(REF.read_text())
word=copy.deepcopy(list(ref)[-1]); inner=list(word)[0]
font=TTFont(FONT); cmap=font.getBestCmap(); glyphs=font.getGlyphSet()
x=3350.456
for ch in '-caption':
    name=cmap[ord(ch)]; pen=SVGPathPen(glyphs); glyphs[name].draw(pen)
    ET.SubElement(inner,NS+'path',{'transform':f'translate({x:.3f} 0)','d':pen.getCommands()})
    x+=font['hmtx'][name][0]+36.864
scale=96/1963
inner.set('transform', f'translate(110.69576379974326 {1512*scale}) scale({scale} {-scale})')
width=110.69576379974326+(x-36.864)*scale+13.4919201314+3
ET.register_namespace('', 'http://www.w3.org/2000/svg')
inverse=approved.replace('#858585','#7a7a7a').replace('#c7c7c7','#383838').replace('stop-color="#ffffff"','stop-color="#000000"').replace('rx="14" fill="#000000"','rx="14" fill="#ffffff"').replace('stroke="#d4d4d4"','stroke="#2b2b2b"')
assets={'icon-primary':approved,'icon-inverse':inverse}
for mode,icon,color in [('primary',approved,'#000000'),('inverse',inverse,'#ffffff')]:
    w=copy.deepcopy(word); list(w)[0].set('fill',color); list(w)[0].set('stroke',color)
    svg=icon.replace('viewBox="0 0 96 96"',f'viewBox="0 0 {width:.6f} 96"')
    svg=svg.replace('</svg>',ET.tostring(w,encoding='unicode')+'\n</svg>')
    svg=svg.replace('Iris Caption — 方案 B（设计草案）','Iris-caption — '+mode+' lockup')
    assets['lockup-'+mode]=svg
for kind,svg in assets.items():
    p=OUT/(PREFIX+kind+'.svg'); assert not p.exists() or p.read_text()==svg,p
    p.write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(),write_to=str(p.with_suffix('.png')),output_width=1024 if kind.startswith('icon') else 3072)
    root=ET.fromstring(svg)
    assert not root.findall('.//'+NS+'text')
    assert [p.attrib['d'] for p in root.findall('.//'+NS+'path')][:4]==[p.attrib['d'] for p in ET.fromstring(approved).findall('.//'+NS+'path')]
assert (OUT/(PREFIX+'icon-primary.svg')).read_bytes()==SOURCE.read_bytes()
for mode in ['primary','inverse']:
    im=Image.open(OUT/(PREFIX+'icon-'+mode+'.png'))
    assert im.size==(1024,1024) and im.mode=='RGBA' and im.getpixel((0,0))[3]==0
icon=Image.open(OUT/(PREFIX+'icon-primary.png'))
icon.save(OUT/(PREFIX+'app.icns'),format='ICNS')
canvas=Image.new('RGB',(1600,1100),'#e9e9e9'); draw=ImageDraw.Draw(canvas)
label=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',24)
heading=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf',36)
draw.text((48,32),'Iris-caption / approved B / v1.0',font=heading,fill='#111111')
for idx,mode in enumerate(['primary','inverse']):
    top=110+idx*490; bg='#ffffff' if idx==0 else '#171717'; fg='#111111' if idx==0 else '#eeeeee'
    draw.rounded_rectangle((32,top,1568,top+460),radius=20,fill=bg)
    draw.text((60,top+22),mode.upper()+' / original Iris v1.4 typography',font=label,fill=fg)
    im=Image.open(io.BytesIO(cairosvg.svg2png(bytestring=assets['lockup-'+mode].encode(),output_width=1420))).convert('RGBA')
    canvas.paste(im,(70,top+80),im)
    draw.text((65,top+325),'Native sizes: 16 / 24 / 32 / 48 / 64 / 128 px',font=label,fill=fg)
    left=710
    for size in [16,24,32,48,64,128]:
        im=Image.open(io.BytesIO(cairosvg.svg2png(bytestring=assets['icon-'+mode].encode(),output_width=size))).convert('RGBA')
        canvas.paste(im,(left,top+300),im); left+=size+28
canvas.save(OUT/(PREFIX+'contact-sheet.png'))
html='<!doctype html><html lang="en"><meta charset="utf-8"><title>Iris-caption logo v1.0</title><style>body{margin:40px;background:#eee;font:16px system-ui}section{padding:32px;margin:24px 0;background:white;border-radius:16px}.inverse{background:#171717;color:white}.lockup{width:min(100%,1200px);display:block;margin:32px 0}.sizes{display:flex;align-items:center;gap:28px}</style><h1>Iris-caption · approved B · v1.0</h1><p>Outlined Arial Rounded MT Bold · 8° right slant · transparent exports</p>'
for mode in ['primary','inverse']:
    html+=f'<section class="{mode}"><h2>{mode}</h2><img class="lockup" alt="Iris-caption" src="{PREFIX}lockup-{mode}.svg"><div class="sizes">'
    for size in [16,24,32,48,64,128]:
        html+=f'<div><img alt="{mode} icon {size}px" width="{size}" height="{size}" src="{PREFIX}icon-{mode}.svg"><p>{size}px</p></div>'
    html+='</div></section>'
(OUT/(PREFIX+'contact-sheet.html')).write_text(html)
report={'approved_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'icon_primary_identical':True,'all_four_icon_paths_unchanged':True,'outlined_wordmark':'Iris-caption','reference_Iris_paths_and_slant_reused':True,'wordmark_scale':scale,'wordmark_tracking_font_units':36.864,'font_sha256':hashlib.sha256(FONT.read_bytes()).hexdigest(),'lockup_viewbox':f'0 0 {width:.6f} 96','icns_sizes':Image.open(OUT/(PREFIX+'app.icns')).info.get('sizes')}
(OUT/(PREFIX+'verification.json')).write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
