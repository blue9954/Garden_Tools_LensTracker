"""Generate synthetic EXIF fixtures for manual UI checks, outside the real library."""
import argparse
import shutil
from pathlib import Path

from PIL import Image
from PIL.TiffImagePlugin import IFDRational


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    for i in range(24):
        exif = Image.Exif()
        exif[271] = 'SONY' if i < 16 else 'FUJIFILM'
        exif[272] = 'ILCE-7M4' if i < 16 else 'X-T5'
        exif[34665] = {42036: 'FE 35mm F1.4 GM' if i < 16 else 'XF 23mm F1.4 R LM WR',
                       36867: f'2026:{1+i%8:02d}:{1+i:02d} 12:30:00',
                       37386: IFDRational(35 if i<16 else 23),33437:IFDRational(14,10),34855:100+i*10}
        Image.new('RGB',(32,32),(i*10,70,100)).save(args.folder/f'test-{i:02d}.jpg',exif=exif)
    shutil.copy2(args.folder/'test-00.jpg',args.folder/'duplicate.jpg')
    Image.new('RGB',(32,32),'gray').save(args.folder/'no-metadata.jpg')
    (args.folder/'broken.jpg').write_bytes(b'intentionally invalid test image')
    print(args.folder.resolve())


if __name__ == '__main__':
    main()
