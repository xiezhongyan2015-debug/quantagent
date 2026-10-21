import sys, time, pythoncom, win32com.client as win32
idx=int(sys.argv[1])
codes=['000001.SZ','000002.SZ','600000.SH','600519.SH','000858.SZ','601318.SH','300750.SZ','002594.SZ'][idx]
pythoncom.CoInitialize()
app=win32.DispatchEx('Excel.Application'); app.Visible=False; app.DisplayAlerts=False; app.EnableEvents=False
wb=None
try:
    wb=app.Workbooks.Add(); ws=wb.Worksheets(1)
    t=time.time()
    for k in range(3):
        ws.Cells.ClearContents()
        ws.Cells(1,1).Formula=f'=WSI("{codes}","open,high,low,close,volume,amt,pct_chg","2024-01-01 09:30:00","2024-12-31 15:00:00","BarSize=1")'
        deadline=time.time()+120
        while time.time()<deadline:
            u=ws.UsedRange
            if u.Rows.Count>1 and str(ws.Cells(1,1).Value)!='Fetching...': break
            time.sleep(0.2)
        print(idx,codes,k,'elapsed',round(time.time()-t,3),'rows',ws.UsedRange.Rows.Count,flush=True)
finally:
    if wb is not None:
        try: wb.Close(SaveChanges=False)
        except: pass
    app.Quit(); pythoncom.CoUninitialize()
