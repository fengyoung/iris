"""Render review-only variants without altering original Iris paths."""
from pathlib import Path
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QFont
from PySide6.QtCore import QRectF
from PySide6.QtSvg import QSvgRenderer
import xml.etree.ElementTree as ET
ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
original = (ROOT/'data/iris-logo/2026-11-05_iris-logo_v1.4_icon-primary.svg').read_text()
cues = {
'A': '<g id="caption-cue" fill="#d4d4d4"><rect x="35" y="72" width="26" height="3" rx="1.5"/><rect x="35" y="79" width="18" height="3" rx="1.5"/></g>',
'B': '<g id="audio-cue" fill="none" stroke="#d4d4d4" stroke-width="3" stroke-linecap="round"><path d="M37 75v3 M44 71v11 M51 73v7 M58 75v3"/></g>',
'C': '<g id="caption-frame-cue" fill="none" stroke="#d4d4d4" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="33" y="69" width="30" height="16" rx="4"/><path d="M39 75h18 M39 79h11"/></g>',
}
files = {'original': 'reference-original.svg'}
(OUT/files['original']).write_text(original)
for k, cue in cues.items():
    files[k] = f'iris-caption-{k}.svg'
    svg = original.replace('<title>Iris — 虹翼</title>', f'<title>Iris Caption — 方案 {k}（设计草案）</title>').replace('</svg>', f'  <!-- Original Iris curves and gradient unchanged. -->\n  {cue}\n</svg>')
    (OUT/files[k]).write_text(svg)
    ns = {'s':'http://www.w3.org/2000/svg'}
    a,b=ET.fromstring(original),ET.fromstring(svg)
    assert [x.attrib for x in a.find('s:g',ns)] == [x.attrib for x in b.find('s:g',ns)]
    assert ET.tostring(a.find('s:defs',ns)) == ET.tostring(b.find('s:defs',ns))
app = QApplication([])
im = QImage(1600,1150,QImage.Format.Format_ARGB32)
im.fill(QColor('#f3f3f5'))
p = QPainter(im)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
def text(x,y,s,size=22,color='#222226',bold=False):
    p.setPen(QColor(color)); f=QFont('PingFang SC',size); f.setBold(bold); p.setFont(f)
    p.drawText(QRectF(x,y,1500-x,55),s)
def icon(key,x,y,size):
    r=QSvgRenderer(str(OUT/files[key])); assert r.isValid(); r.render(p,QRectF(x,y,size,size))
text(56,28,'Iris Caption · 标志微变体',32,bold=True)
text(58,85,'保留已确认的虹翼曲线与银灰渐变，仅在图形下方加入功能提示。',19,'#66666e')
names=['原版 · Iris','A · 双行字幕','B · 轻声波','C · 字幕框']
notes=['已确认的基准图标','推荐：轻量、直接、克制','更偏音频输入与转写','字幕含义更明确，细节较多']
for top,bg,fg,muted,label in [(160,'#ffffff','#202024','#73737b','浅色背景'),(615,'#202024','#f4f4f5','#a4a4ae','深色背景')]:
    p.setPen(QColor(bg)); p.setBrush(QColor(bg)); p.drawRoundedRect(QRectF(32,top,1536,426),22,22)
    text(56,top+13,label,16,muted)
    for col,key in enumerate(files):
        x=68+col*380
        text(x,top+49,names[col],23,fg,True)
        icon(key,x+88,top+104,160)
        icon(key,x+100,top+292,64); icon(key,x+210,top+308,32)
        text(x+105,top+357,'64 px',13,muted); text(x+205,top+357,'32 px',13,muted)
        text(x+8,top+389,notes[col],16,muted)
text(58,1080,'设计评审稿 · 2026-11-05 · 尚未应用到产品；小尺寸为实际 32 / 64 像素。',17,'#66666e')
p.end(); assert im.save(str(OUT/'contact-sheet.png'))
for key in files:
    for size in (32,64):
        image=QImage(size,size,QImage.Format.Format_ARGB32); image.fill(QColor('transparent'))
        painter=QPainter(image); QSvgRenderer(str(OUT/files[key])).render(painter); painter.end()
        assert image.save(str(OUT/f'{key}-{size}.png'))
print('Rendered 4 SVGs, contact sheet and 8 small PNGs; original paths and gradient verified unchanged.')
