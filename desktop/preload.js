const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("halohubx", {
  isElectron: true,
  platform: process.platform,
});
