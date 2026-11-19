import xml.etree.ElementTree as ET, re, html, sys, os
from PIL import Image, ImageDraw, ImageFont
F='/usr/share/fonts/truetype/nanum/'
fn=[f for f in os.listdir(F) if f.startswith('NanumGothic') and f.endswith('.ttf')]
font=ImageFont.truetype(F+(fn[0] if fn else 'NanumSquareL.ttf'),11)
def txt(v): return re.sub('<[^>]+>','',html.unescape(re.sub('<br>|<br/>|</div><div>','\n',v or '')))
def st(s): return dict(p.split('=',1) for p in (s or '').split(';') if '=' in p)
src,prefix,out=sys.argv[1:4]
for d in ET.parse(src).getroot().findall('diagram'):
    if not d.get('name').startswith(prefix): continue
    cs={c.get('id'):c for c in d.iter('mxCell')}
    G={}
    for k,c in cs.items():
        g=c.find('mxGeometry')
        if c.get('vertex') and g is not None: G[k]=[float(g.get(a) or 0) for a in ('x','y','width','height')]
    xs=[v[0] for v in G.values()]+[v[0]+v[2] for v in G.values()]; ys=[v[1] for v in G.values()]+[v[1]+v[3] for v in G.values()]
    ox,oy=min(xs)-20,min(ys)-20; W,H=int(max(xs)-ox+40),int(max(ys)-oy+40)
    sc=1.4; im=Image.new('RGB',(int(W*sc),int(H*sc)),'white'); dr=ImageDraw.Draw(im)
    T=lambda x,y:((x-ox)*sc,(y-oy)*sc)
    for k,(x,y,w,h) in G.items():
        s=cs[k].get('style') or ''; S=st(s)
        col=S.get('strokeColor','#000'); col='#000000' if col in('none','') else col
        if 'text;' in s or s.startswith('text'): pass
        elif 'ellipse' in s: dr.ellipse([*T(x,y),*T(x+w,y+h)],outline='black',fill='black' if 'endState' not in s else 'white',width=2)
        elif 'rhombus' in s: dr.polygon([T(x+w/2,y),T(x+w,y+h/2),T(x+w/2,y+h),T(x,y+h/2)],outline='black')
        else: dr.rectangle([*T(x,y),*T(x+w,y+h)],outline=col,fill=S.get('fillColor','#ffffff') if S.get('fillColor','none')!='none' else None)
        if 'endState' in s: dr.ellipse([*T(x+5,y+5),*T(x+w-5,y+h-5)],fill='black')
        t=txt(cs[k].get('value'))
        if t: dr.multiline_text(T(x+4,y+3),t[:400],fill='black',font=font)
    def pt(k,c,pre):
        x,y,w,h=G[k]; S=st(c.get('style'))
        if pre+'X' in S: return (x+w*float(S[pre+'X']),y+h*float(S[pre+'Y']))
        return (x+w/2,y+h/2)
    for k,c in cs.items():
        if not c.get('edge'): continue
        s,t=c.get('source'),c.get('target')
        if s not in G or t not in G: print('dangling',k,s,t); continue
        a=pt(s,c,'exit'); b=pt(t,c,'entry')
        g=c.find('mxGeometry'); pts=[(float(p.get('x')),float(p.get('y'))) for ar in g.findall('Array') for p in ar]
        seq=[a]+pts+[b]; path=[seq[0]]
        for q in seq[1:]:
            p=path[-1]
            if p[0]!=q[0] and p[1]!=q[1]: path.append((q[0],p[1]) if not pts or True else q)
            path.append(q)
        dash='dashed=1' in (c.get('style') or '')
        dr.line([T(*p) for p in path],fill='#888888' if dash else 'red',width=1)
        bx,by=T(*b); dr.ellipse([bx-3,by-3,bx+3,by+3],fill='red')
        lab=txt(c.get('value'))
        if lab:
            m=path[len(path)//2-1]; n=path[len(path)//2]; mx,my=T((m[0]+n[0])/2,(m[1]+n[1])/2)
            bb=dr.multiline_textbbox((mx,my),lab,font=font); dr.rectangle(bb,fill='#ffffcc'); dr.multiline_text((mx,my),lab,fill='blue',font=font)
    im.save(out); print(out,im.size)
