// Ví thử nghiệm chuẩn EIP-1193 (thay MetaMask khi test tự động).
// Trả về tài khoản __ACCOUNT__ và chuyển MỌI request JSON-RPC tới Hardhat node – node tự ký cho các
// tài khoản mặc định (đã mở khóa). Dashboard dùng đúng đường code ethers BrowserProvider như với MetaMask.
(() => {
  const RPC = "__RPC__";
  const ACCOUNT = "__ACCOUNT__";
  const listeners = {};
  let id = 0;
  window.ethereum = {
    isHrcTestWallet: true,
    async request({ method, params }) {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [ACCOUNT];
      if (method === "wallet_switchEthereumChain" || method === "wallet_addEthereumChain") return null;
      const res = await fetch(RPC, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", id: ++id, method, params: params ?? [] }),
      });
      const body = await res.json();
      if (body.error) {
        const err = new Error(body.error.message);
        err.code = body.error.code;
        err.data = body.error.data;
        throw err;
      }
      return body.result;
    },
    on(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    removeListener(ev, fn) { listeners[ev] = (listeners[ev] || []).filter((f) => f !== fn); },
  };
})();
