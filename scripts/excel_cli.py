"""既存CLIのExcel指定時だけ使う共通記録・一時保存・公開処理。"""

from datetime import datetime
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import portable_converter as app


def run(operation, args):
    try:
        if operation == "shaft" and args.output and not args.profile:
            raise app.InputError("モデル出力時は --profile existing-screen を指定してください。")
        request = app.Request(args.sdc,args.ndu,operations=(operation,),groups=tuple(args.groups),
                              shaft_profile="existing-screen" if operation=="shaft" else None,
                              push_direction=getattr(args,"push_direction","right"),
                              horizontal_cross_layer=args.cross_layer if operation=="horizontal" else "length-weighted",
                              pressure_cross_layer=args.cross_layer if operation=="pressure" else "integral-average",
                              pressure_decimals=getattr(args,"decimals",1),
                              shaft_k_decimals=getattr(args,"k_decimals",0),
                              shaft_force_decimals=getattr(args,"force_decimals",1))
        plan=app.prepare(request)
        output = plan.target if getattr(args,"write",False) else getattr(args,"output",None)
        output = Path(output).resolve() if output else None
        excel = Path(args.excel_report).resolve()
        json_path = Path(args.report).resolve() if getattr(args,"report",None) else None
        if excel.suffix.lower() != ".xlsx": raise app.InputError("Excel報告書は.xlsxで指定してください。")
        if output and output.suffix.lower() != ".ndu": raise app.InputError("モデル出力は.nduで指定してください。")
        if json_path and json_path.suffix.lower() != ".json": raise app.InputError("JSON報告書は.jsonで指定してください。")
        overwrite=bool(getattr(args,"write",False))
        backup=plan.target.with_name(plan.target.name+".bak") if overwrite and plan.data!=dict(plan.sources)[plan.target] else None
        inputs=[p for p,_ in plan.sources]
        reference=getattr(args,"reference",None)
        if reference:
            from dataclasses import replace
            ref_path=app.input_path(reference,".ndu")
            ref_data=ref_path.read_bytes()
            inputs.append(ref_path)
            plan=replace(plan,sources=(*plan.sources,(ref_path,ref_data)))
            plan.report["sources"].append(dict(path=str(ref_path),sha256=app.digest(ref_data),role="comparison-only"))
            _,comparison=app.pressure.make_plan(app.base.parse_ndu(dict(plan.sources)[plan.target]),
                                               app.pressure.parse_pressure_sdc(dict(plan.sources)[request.sdc.resolve()]),
                                               app.base.parse_groups(args.groups),args.push_direction,args.decimals,
                                               args.cross_layer,app.base.parse_ndu(ref_data))
            plan.report["reference_comparison"]=comparison
        outputs=[p for p in (output,excel,json_path,backup) if p]
        for i,p in enumerate(outputs):
            if not p.parent.is_dir(): raise app.InputError(f"保存先フォルダーがありません: {p}")
            if any(app.support.same_path(p,q) for q in outputs[i+1:]): raise app.InputError("モデル・Excel・JSON・バックアップは別々のパスを指定してください。")
            if any(app.support.same_path(p,q) and not (overwrite and p==output and q==plan.target) for q in inputs):
                raise app.InputError(f"入力ファイルと出力先が同じ実体です: {p}")
        if excel.exists(): raise app.InputError(f"Excelは既にあります。新しい名前を指定してください: {excel}")
        if backup and backup.exists(): raise app.InputError(f"既存バックアップは上書きしません: {backup}")
        if output and not overwrite and output.exists() and output.read_bytes()!=plan.data:
            raise app.InputError(f"異なる既存ファイルは上書きしません: {output}")
        stamp=datetime.now().strftime("%Y%m%d-%H%M%S-%f")+"-"+uuid4().hex[:8]
        report=dict(plan.report,run_id=stamp,mode="saved" if output else "preview",output=str(output) if output else None,
                    report_path=str(json_path) if json_path else None,excel_path=str(excel),backup=str(backup) if backup else None)
        if output: report["saved_at"]=datetime.now().astimezone().isoformat()
        # 既存CLI報告書の対象一覧・配置キーを残し、共通記録を付加する。
        report.update(plan.report["details"][operation])
        if operation in ("shaft","tip"):
            report["field_names"]=list(app.support.FIELD_NAMES)
        if operation=="shaft": report["configuration"]["profile_explicit"]=bool(args.profile)
        book=app.excel_report.build(report)
        excel_data=book.to_xlsx()
        report["excel_sha256"]=app.digest(excel_data)
        json_data=(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n").encode("utf8")
        artifacts=[(excel,excel_data)] + ([(json_path,json_data)] if json_path else [])
        for p,data in artifacts:
            if p.exists() and p.read_bytes()!=data: raise app.InputError(f"異なる既存ファイルは上書きしません: {p}")
        app.verify_sources(plan)
        pending=[]
        published=[]
        try:
            staged_output=app.stage(output,plan.data) if output else None
            if staged_output:pending.append(staged_output)
            staged_artifacts=[]
            for p,data in artifacts:
                temp=app.stage(p,data)
                pending.append(temp)
                staged_artifacts.append((temp,p,data))
            if backup:
                temp=app.stage(backup,dict(plan.sources)[plan.target])
                pending.append(temp)
                app.publish_new(temp,backup)
            for temp,p,data in staged_artifacts:
                if p!=excel and p.exists() and p.read_bytes()==data: continue
                identity=temp.stat()
                app.publish_new(temp,p)
                published.append((p,identity.st_dev,identity.st_ino,data))
            app.verify_sources(plan)
            if output:
                if overwrite:
                    if backup: os.replace(staged_output,output)
                elif output.exists():
                    if output.read_bytes()!=plan.data: raise app.InputError(f"出力先が変更されました: {output}")
                else:
                    app.publish_new(staged_output,output)
        except BaseException as exc:
            for p,dev,ino,data in reversed(published):
                try:
                    current=p.stat()
                    if (current.st_dev,current.st_ino)==(dev,ino) and p.read_bytes()==data: p.unlink()
                except OSError: exc.add_note(f"帳票を取り消せませんでした: {p}")
            if backup and backup.exists():exc.add_note(f"バックアップは保持しています: {backup}")
            raise
        finally:
            for p in pending:p.unlink(missing_ok=True)
        print(f"Excel: {excel}")
        print(f"モデル: {output}" if output else "計算確認・モデル未保存")
        if json_path:print(f"JSON: {json_path}")
        return 0
    except (ValueError,OSError,UnicodeError,ArithmeticError) as exc:
        print("\n".join([f"エラー: {exc}",*getattr(exc,"__notes__",[])]),file=sys.stderr)
        return 1
