from pathlib import Path
import runpy,sys
ROOT=Path(__file__).resolve().parents[2]
def main():
    target=ROOT/'octopuss'/'commi3_watch.py'
    runpy.run_path(str(target),run_name='__main__')
    return 0
if __name__=='__main__': raise SystemExit(main())
