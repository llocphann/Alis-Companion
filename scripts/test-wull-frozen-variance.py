#!/usr/bin/env python3
"""Independent channel fixtures for frozen analysis, never a GPU qualification."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from PIL import Image

spec=importlib.util.spec_from_file_location('frozen',Path(__file__).with_name('wull-frozen-variance.py'))
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
with tempfile.TemporaryDirectory(prefix='frozen-variance-') as temporary:
    root=Path(temporary)
    a=bytes([0,0,0,0,10,20,30,255,80,90,100,128,1,2,3,0])
    b=bytes([9,0,0,0,10,25,30,255,81,90,100,129,1,2,3,5])
    c=b[:-1]+bytes([6])
    row={'name':'literal_fixture','changed_pixels':4,'baseline_repeat_changed_pixels':0,
         'candidate_repeat_changed_pixels':1}
    for side,raw in [('baseline',a),('candidate',b)]:
        for suffix,pixels,key in [('',raw,side+'_rgba_sha256'),('-repeat',a if side=='baseline' else c,side+'_repeat_rgba_sha256')]:
            Image.frombytes('RGBA',(4,1),pixels).save(root/(side+'-0'+suffix+'.png'));row[key]=api.digest(pixels)
    receipt={'schema':3,'cases':1,'comparison':[row],'status':'INCONCLUSIVE_CAPTURE_VARIANCE',
        'baseline_source_sha256':'a'*64,'candidate_source_sha256':'a'*64,
        'baseline_qsb_sha256':'b'*64,'candidate_qsb_sha256':'b'*64}
    path=root/'result.json';path.write_text(json.dumps(receipt))
    result=api.analyze(path);stats=result['cases'][0]['pairs']['cross']
    assert stats=={'changedPixels':4,'alphaChanged':2,'rgbChanged':3,'transparentRgbOnly':1,
        'visibleRgbOnly':1,'alphaOnly':1,'alphaAndRgb':1,'opaqueChanged':1,'nonOpaqueChanged':3,
        'maxDelta':9,'bbox':[0,0,4,1]}
    assert result['sameSourceAndQsb'] and not result['qualifiesOriginalG0'] and not result['capturedNewFrames']
    assert result['totals']['candidateRepeat']['changedPixels']==1
    def rejected(value):
        path.write_text(json.dumps(value))
        try:api.analyze(path)
        except ValueError:return
        raise AssertionError('unverified frozen input accepted')
    rejected({**receipt,'schema':2})
    rejected({**receipt,'baseline_qsb_sha256':None,'candidate_qsb_sha256':None})
    forged=copy.deepcopy(receipt);forged['comparison'][0]['baseline_rgba_sha256']='0'*64;rejected(forged)
    forged=copy.deepcopy(receipt);forged['comparison'][0]['changed_pixels']=0;rejected(forged)
    (root/'baseline-0-repeat.png').unlink();rejected(receipt)
print('COMPANION_FROZEN_VARIANCE_PASS strictRGBA transparentRGB alpha interior repeatHash reportedCounts staleSchema missingInput noCapture noQualification')
