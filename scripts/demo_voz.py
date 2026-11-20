"""Muestras de voz para comparar: varias voces de Gemini y la de edge-tts de siempre, con el mismo guion."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import daily  # noqa: E402
import voz  # noqa: E402

GUION = ["¿Frenas fuerte y aun así se te va el coche en la curva?",
         "El truco está en soltar el freno poco a poco mientras giras.",
         "Así el morro tiene agarre y el coche entra donde tú quieres.",
         "Pruébalo hoy en tu circuito favorito y nota la diferencia."]
OUT = Path("docs/demo/voz")
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)


def join(parts, out):
    lst = Path(tempfile.mkdtemp()) / "l.txt"
    lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-af", "apad=pad_dur=0.25," + daily.VOICE_FX, "-b:a", "160k", str(out)], check=True)


for gender, name in [("hombre", "Puck"), ("hombre", "Fenrir"), ("hombre", "Charon"), ("mujer", "Zephyr"), ("mujer", "Leda"), ("mujer", "Kore")]:
    voz.VOCES[gender] = name
    try:
        r = voz.lines(GUION, gender, "normal", tempfile.mkdtemp(), print)
        join([x[0] for x in r], OUT / f"gemini-{name}.mp3")
    except Exception as e:  # noqa: BLE001
        print(f"✗ {name}: {e}")
for name, v in [("edge-Alvaro", "es-ES-AlvaroNeural"), ("edge-Elvira", "es-ES-ElviraNeural")]:
    td = Path(tempfile.mkdtemp())
    parts = []
    for k, t in enumerate(GUION):
        daily.tts(t, v, "+8%", td / f"{k}.mp3")
        parts.append(td / f"{k}.mp3")
    join(parts, OUT / f"{name}.mp3")
print(sorted(p.name for p in OUT.glob("*.mp3")))
