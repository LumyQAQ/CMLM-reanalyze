from mootdx.quotes import Quotes

# 我们单独测试拉取博敏电子(603936)的 K 线
code = '603936'
print(f"🕵️ 正在测试拉取 {code} 的历史 K 线...")

client = Quotes.factory(market='std')
try:
    df_k = client.bars(symbol=code, frequency=9, offset=60)
    if df_k is None or df_k.empty:
        df_k = client.bars(symbol='sh' + code, frequency=9, offset=60)

    print("\n📊 拉取结果：")
    if df_k is not None and not df_k.empty:
        print(df_k[['datetime', 'close', 'vol', 'amount']].tail())
        print("\n✅ 诊断结论：K线接口连通正常！(说明是逻辑过滤太严)")
    else:
        print("\n❌ 诊断结论：K线数据完全为空！(mootdx 历史节点已失效或被封锁)")
except Exception as e:
    print(f"\n💥 诊断结论：代码执行崩溃，真实报错信息为 -> {e}")

client.client.close()
