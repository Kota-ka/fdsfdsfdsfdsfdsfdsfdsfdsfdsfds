import sys, math, subprocess
from pathlib import Path
from PIL import Image, ImageDraw
sys.path.insert(0,str(Path.cwd()))
from test_stream_replay import replay_stream
out=Path('artifacts')
traces=[]
for title,args in [('JUMPS - 120 BPM',dict(bpm=120,jump=True,rate=1)),('STREAM - ritmo variavel',dict(bpm=240,rate=1.2,variable=True))]:
    trace=[]
    count,hits=replay_stream(**args,trace=trace)
    print(title,len(hits),'/',count,'hits; maximum timing error',max(abs(x[0]) for x in hits))
    traces.append((title,args.get('rate',1.5),trace))
width,height=960,600
output=out/'simulacao-trajetorias.mp4'
cmd=['ffmpeg','-y','-hide_banner','-loglevel','error','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{width}x{height}','-framerate','60','-i','-','-an','-c:v','libx264','-pix_fmt','yuv420p','-crf','20',str(output)]
process=subprocess.Popen(cmd,stdin=subprocess.PIPE)
def screen(point):
    return (int(90+(point[0]-384)/2.25*1.5),int(95+(point[1]-126)/2.25*1.05))
for title,rate,trace in traces:
    # Replays use a real 1 ms clock; the preview is shown at half speed.
    for frame in range(math.ceil(len(trace)/1000*2*60)):
        tick=min(len(trace)-1,round(frame/60*500))
        _,cursor,objects=trace[tick]
        im=Image.new('RGB',(width,height),(12,17,24))
        d=ImageDraw.Draw(im)
        d.text((24,18),'SIMULACAO DO MOTOR ATUAL | entradas do Windows e tosu simuladas',fill=(225,230,238))
        d.text((24,40),title+' | reproducao 0.5x | rosa = cursor',fill=(143,159,181))
        d.rectangle((90,95,858,498),outline=(39,50,66),width=1)
        live=tick*rate
        for index,obj in enumerate(objects):
            if live-120<=obj['time']<=live+500:
                p=screen((384+obj['x']*2.25,126+(384-obj['y'])*2.25))
                r=19
                color=(120,135,155) if live<obj['time'] else (55,110,95)
                d.ellipse((p[0]-r,p[1]-r,p[0]+r,p[1]+r),outline=color,width=2)
                d.text((p[0]-5,p[1]-6),str(index+1),fill=color)
        tail=trace[max(0,tick-300):tick+1:3]
        for i in range(1,len(tail)):
            alpha=i/len(tail)
            color=(int(45+210*alpha),int(25+95*alpha),int(55+150*alpha))
            d.line((screen(tail[i-1][1]),screen(tail[i][1])),fill=color,width=3)
        cx,cy=screen(cursor)
        d.ellipse((cx-5,cy-5,cx+5,cy+5),fill=(255,180,223))
        d.text((24,545),'Trajeto calculado pelo pipeline AimAssist + Playback; nao e uma partida real.',fill=(143,159,181))
        process.stdin.write(im.tobytes())
process.stdin.close()
assert process.wait()==0
subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(output),'-vf','fps=20,scale=720:-1,split[a][b];[a]palettegen[p];[b][p]paletteuse','-loop','0',str(out/'simulacao-trajetorias.gif')],check=True)
print(output.resolve())
