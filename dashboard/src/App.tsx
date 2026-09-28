import { useCallback, useEffect, useMemo, useState } from "react";

import { useBackend } from "./backend";
import type { ChainRecord, OnchainRobot, RoleName, Wallet } from "./chain";
import { addrUrl, connectWallet, describeError, loadHistory, readContract, readRobot, short, unlockRobot } from "./chain";
import { RobotPanel, type UnlockUi } from "./components/RobotPanel";
import { ActivityFeed, BatchTable, IncidentTable } from "./components/Tables";

const CHAIN_POLL_MS = 4000;
const NETWORK_NAME: Record<number, string> = { 31337: "Hardhat local", 11155111: "Sepolia" };

export default function App() {
  const be = useBackend();
  const cfg = be.config;
  const reader = useMemo(() => (cfg ? readContract(cfg) : null), [cfg]);
  const [onchain, setOnchain] = useState<Record<number, OnchainRobot>>({});
  const [history, setHistory] = useState<ChainRecord[]>([]);
  const [chainErr, setChainErr] = useState<string | null>(null);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [walletErr, setWalletErr] = useState<string | null>(null);
  const [unlock, setUnlock] = useState<Record<number, UnlockUi>>({});

  // Đọc chuỗi định kỳ + ngay khi backend báo có event on-chain
  const refreshChain = useCallback(async () => {
    if (!cfg || !reader) return;
    try {
      const robots = await Promise.all(cfg.robot_ids.map((rid) => readRobot(reader, rid)));
      setOnchain(Object.fromEntries(cfg.robot_ids.map((rid, i) => [rid, robots[i]])));
      setHistory(await loadHistory(reader, cfg.start_block));
      setChainErr(null);
    } catch (e) {
      setChainErr(describeError(e));
    }
  }, [cfg, reader]);

  useEffect(() => {
    refreshChain();
    const t = setInterval(refreshChain, CHAIN_POLL_MS);
    return () => clearInterval(t);
  }, [refreshChain, be.chainTick]);

  // Đổi tài khoản / mạng trong MetaMask => ngắt kết nối, người dùng kết nối lại
  useEffect(() => {
    const eth = window.ethereum;
    if (!eth?.on) return;
    const reset = () => setWallet(null);
    eth.on("accountsChanged", reset);
    eth.on("chainChanged", reset);
    return () => {
      eth.removeListener?.("accountsChanged", reset);
      eth.removeListener?.("chainChanged", reset);
    };
  }, []);

  const connect = async () => {
    if (!cfg || !reader) return;
    setWalletErr(null);
    try {
      setWallet(await connectWallet(cfg, reader));
    } catch (e) {
      setWalletErr(describeError(e));
    }
  };

  const doUnlock = async (rid: number) => {
    if (!cfg || !wallet) return;
    setUnlock((u) => ({ ...u, [rid]: { phase: "signing" } }));
    try {
      const r = await unlockRobot(wallet, cfg, rid, (hash) => setUnlock((u) => ({ ...u, [rid]: { phase: "pending", hash } })));
      setUnlock((u) => ({ ...u, [rid]: { phase: "done", hash: r.hash } }));
      refreshChain();
    } catch (e) {
      setUnlock((u) => ({ ...u, [rid]: { ...u[rid], phase: "error", error: describeError(e) } }));
    }
  };

  const roles = wallet ? (Object.keys(wallet.roles) as RoleName[]).filter((r) => wallet.roles[r] && r !== "DEVICE") : [];
  const wsTone = be.wsStatus === "open" ? "good" : be.wsStatus === "connecting" ? "warning" : "critical";

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <strong>HRC Safety Log</strong>
          <span>Hộp đen an toàn cho robot hợp tác · Nhóm 01</span>
        </div>
        <div className="chips">
          <span className="chip" data-testid="ws-status"><span className={`dot ${wsTone}`} />Backend {be.wsStatus === "open" ? "real-time" : be.wsStatus === "connecting" ? "đang kết nối" : "mất kết nối"}</span>
          <span className="chip"><span className={`dot ${be.state?.mqtt_connected ? "good" : "critical"}`} />MQTT</span>
          {cfg && (
            <span className="chip" title={cfg.contract_address}>
              <span className={`dot ${chainErr ? "critical" : "good"}`} />
              {NETWORK_NAME[cfg.chain_id] ?? `chain ${cfg.chain_id}`} ·{" "}
              {addrUrl(cfg, cfg.contract_address)
                ? <a href={addrUrl(cfg, cfg.contract_address)!} target="_blank" rel="noreferrer" className="mono">{short(cfg.contract_address, 4)} ↗</a>
                : <span className="mono">{short(cfg.contract_address, 4)}</span>}
            </span>
          )}
          {wallet ? (
            <span className="chip" data-testid="wallet">
              <span className="dot good" />
              <span className="mono" data-testid="wallet-address">{short(wallet.address, 4)}</span>
              {roles.length ? roles.map((r) => <strong key={r} data-testid={`role-${r}`}>{r}</strong>) : <span>không có vai trò</span>}
            </span>
          ) : (
            <button className="btn primary" onClick={connect} disabled={!cfg} data-testid="connect-wallet">Kết nối MetaMask</button>
          )}
        </div>
      </header>

      <main>
        {walletErr && <p className="err" data-testid="wallet-error">{walletErr}</p>}
        {!cfg && <section className="card"><h2>Đang chờ backend…</h2><p className="sub">Chạy <code>hrc-backend</code> (cổng 8000) rồi tải lại trang.</p></section>}
        {cfg && be.state?.robots.map((r) => (
          <RobotPanel key={r.robot_id} cfg={cfg} robot={r} onchain={onchain[r.robot_id]} series={be.series[r.robot_id] ?? []}
                      wallet={wallet} unlock={unlock[r.robot_id] ?? { phase: "idle" }} onUnlock={() => doUnlock(r.robot_id)} />
        ))}
        {cfg && (
          <div className="two-col">
            <IncidentTable cfg={cfg} rows={history} error={chainErr} />
            <ActivityFeed cfg={cfg} items={be.activity} />
          </div>
        )}
        {cfg && <BatchTable cfg={cfg} rows={be.batches} />}
      </main>
    </>
  );
}
