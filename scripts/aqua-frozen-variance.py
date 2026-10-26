#!/usr/bin/env python3
"""Read frozen G0 PNGs and receipts. Never capture, replay or qualify a GPU run."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from PIL import Image


def digest(raw):return hashlib.sha256(raw).hexdigest()


def pixels(path, expected, pins):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>16*1024*1024:
            raise ValueError('png_unavailable')
        raw=path.read_bytes()
        with Image.open(BytesIO(raw)) as image:
            if image.format!='PNG' or image.mode!='RGBA' or image.width*image.height>2*1024*1024:
                raise ValueError('unsupported_png')
            size=image.size;rgba=image.tobytes()
    except (OSError,Image.DecompressionBombError):
        raise ValueError('png_unavailable') from None
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected) or digest(rgba)!=expected:
        raise ValueError('rgba_hash_mismatch')
    pins[path]=digest(raw)
    return size,rgba


def compare(left,right,size):
    if len(left)!=len(right) or len(left)!=size[0]*size[1]*4:raise ValueError('size_mismatch')
    result={key:0 for key in ['changedPixels','alphaChanged','rgbChanged','transparentRgbOnly',
        'visibleRgbOnly','alphaOnly','alphaAndRgb','opaqueChanged','nonOpaqueChanged','maxDelta']}
    bounds=[size[0],size[1],-1,-1]
    for offset in range(0,len(left),4):
        a,b=left[offset:offset+4],right[offset:offset+4]
        if a==b:continue
        alpha=a[3]!=b[3];rgb=a[:3]!=b[:3];x=(offset//4)%size[0];y=(offset//4)//size[0]
        result['changedPixels']+=1;result['alphaChanged']+=alpha;result['rgbChanged']+=rgb
        result['transparentRgbOnly']+=rgb and a[3]==b[3]==0
        result['visibleRgbOnly']+=rgb and a[3]==b[3] and a[3]>0
        result['alphaOnly']+=alpha and not rgb;result['alphaAndRgb']+=alpha and rgb
        result['opaqueChanged']+=a[3]==b[3]==255
        result['nonOpaqueChanged']+=not(a[3]==b[3]==255)
        result['maxDelta']=max(result['maxDelta'],*(abs(x-y) for x,y in zip(a,b)))
        bounds=[min(bounds[0],x),min(bounds[1],y),max(bounds[2],x+1),max(bounds[3],y+1)]
    result['bbox']=bounds if result['changedPixels'] else None
    return result


def analyze(receipt):
    receipt=Path(receipt).resolve()
    if receipt.stat().st_size>2*1024*1024:raise ValueError('receipt_too_large')
    raw=receipt.read_bytes();source=json.loads(raw)
    rows=source.get('comparison');count=source.get('cases')
    if source.get('schema')!=3 or type(count) is not int or not 1<=count<=64 or not isinstance(rows,list) or len(rows)!=count:
        raise ValueError('frozen_comparison_unavailable')
    for key in ['baseline_source_sha256','candidate_source_sha256','baseline_qsb_sha256','candidate_qsb_sha256']:
        if not isinstance(source.get(key),str) or not re.fullmatch('[0-9a-f]{64}',source[key]):
            raise ValueError('source_pin_missing')
    pins={receipt:digest(raw)};cases=[];seen=set()
    for index,row in enumerate(rows):
        name=row.get('name') if isinstance(row,dict) else None
        if not isinstance(name,str) or not 0<len(name)<=120 or name in seen:raise ValueError('invalid_case')
        seen.add(name);images={}
        for side in ('baseline','candidate'):
            for suffix,key in [('',side+'_rgba_sha256'),('-repeat',side+'_repeat_rgba_sha256'),
                    ('-warmup',side+'_warmup_rgba_sha256')]:
                if suffix=='-warmup' and key not in row:continue
                images[side+suffix]=pixels(receipt.parent/(side+'-'+str(index)+suffix+'.png'),row.get(key),pins)
        pairs={}
        for label,a,b,reported in [('cross','baseline','candidate','changed_pixels'),
                ('baselineRepeat','baseline','baseline-repeat','baseline_repeat_changed_pixels'),
                ('candidateRepeat','candidate','candidate-repeat','candidate_repeat_changed_pixels')]:
            if images[a][0]!=images[b][0]:raise ValueError('size_mismatch')
            result=compare(images[a][1],images[b][1],images[a][0])
            if result['changedPixels']!=row.get(reported):raise ValueError('reported_count_mismatch')
            pairs[label]=result
        cases.append({'name':name,'size':list(images['baseline'][0]),'pairs':pairs})
    # Verify the entire consumed input set again. Nothing is written alongside
    # it, no capture permission is granted and no old lineage is consumed anew.
    if any(not path.is_file() or digest(path.read_bytes())!=value for path,value in pins.items()):
        raise ValueError('frozen_input_changed')
    totals={label:{key:sum(case['pairs'][label][key] for case in cases)
        for key in ['changedPixels','alphaChanged','rgbChanged','transparentRgbOnly','visibleRgbOnly',
                    'alphaOnly','alphaAndRgb','opaqueChanged','nonOpaqueChanged']} for label in ('cross','baselineRepeat','candidateRepeat')}
    return {'schema':2,'status':'ANALYZED_FROZEN_INPUTS_NOT_A_NEW_G0_RUN',
        'originalStatus':source.get('status'),'receiptSha256':digest(raw),
        'baselineSourceSha256':source.get('baseline_source_sha256'),
        'candidateSourceSha256':source.get('candidate_source_sha256'),
        'sameSourceAndQsb':source.get('baseline_source_sha256')==source.get('candidate_source_sha256')
            and source.get('baseline_qsb_sha256')==source.get('candidate_qsb_sha256'),
        'inputFiles':[{'name':path.name,'sha256':value} for path,value in sorted(pins.items())],
        'sourceInputsUnchanged':True,'capturedNewFrames':False,'qualifiesOriginalG0':False,
        'cases':cases,'totals':totals}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve()
    if output.is_relative_to(args.receipt.resolve().parent):raise ValueError('output_inside_frozen_inputs')
    result=analyze(args.receipt)
    with output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    output.chmod(0o600)
    print(json.dumps({key:result[key] for key in ['status','originalStatus','sourceInputsUnchanged','qualifiesOriginalG0','totals']}))


if __name__=='__main__':main()
