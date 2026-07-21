const fs = require("fs");
const os = require("os");
const path = require("path");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");

const BRIDGE_URL = process.env.WHATSAPP_AGENT_URL || "http://127.0.0.1:5051/whatsapp/message";
const BRIDGE_BASE = BRIDGE_URL.replace(/\/message\/?$/, "");
const BRIDGE_TOKEN = (process.env.WHATSAPP_BRIDGE_TOKEN || "").trim();
const OUTBOX_POLL_MS = parseInt(process.env.OUTBOX_POLL_MS || "4000", 10);
const SESSION_NAME = process.env.WWEBJS_SESSION_NAME || "default";
const AUTH_PATH = path.join(__dirname, ".wwebjs_auth");

// Puppeteer surumu degistiginde istedigi Chrome revizyonu degisir; indirilen
// yeni revizyon eksik/bozuk olabilir (Framework'suz "Failed to launch"). Bunu
// onlemek icin puppeteer cache'inde kurulumu TAM (Framework'u yerinde) bir
// Chrome bulup ona sabitleriz. PUPPETEER_EXECUTABLE_PATH verilmisse o onceliklidir.
function resolveChromePath() {
  const envPath = (process.env.PUPPETEER_EXECUTABLE_PATH || "").trim();
  if (envPath && fs.existsSync(envPath)) {
    return envPath;
  }
  const base = path.join(os.homedir(), ".cache", "puppeteer", "chrome");
  let dirs = [];
  try {
    dirs = fs.readdirSync(base).filter((name) => name.startsWith("mac_arm-"));
  } catch (error) {
    return undefined; // mac disi ortam / cache yok -> puppeteer varsayilanina birak
  }
  dirs.sort().reverse(); // en yeni revizyonu once dene
  for (const dir of dirs) {
    const contents = path.join(base, dir, "chrome-mac-arm64", "Google Chrome for Testing.app", "Contents");
    const binary = path.join(contents, "MacOS", "Google Chrome for Testing");
    const framework = path.join(contents, "Frameworks", "Google Chrome for Testing Framework.framework");
    if (fs.existsSync(binary) && fs.existsSync(framework)) {
      return binary; // kurulumu tam olan ilk Chrome
    }
  }
  return undefined;
}

const CHROME_PATH = resolveChromePath();
if (CHROME_PATH) {
  console.log(`Chrome (Puppeteer): ${CHROME_PATH}`);
} else {
  console.warn("Uygun indirilmis Chrome bulunamadi; Puppeteer varsayilani denenecek.");
}

const client = new Client({
  authStrategy: new LocalAuth({
    clientId: SESSION_NAME,
    dataPath: AUTH_PATH
  }),
  puppeteer: {
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
    ...(CHROME_PATH ? { executablePath: CHROME_PATH } : {})
  }
});

function normalizeSender(rawSender) {
  return String(rawSender || "")
    .replace(/@(c\.us|s\.whatsapp\.net)$/i, "")
    .trim();
}

async function resolveSenderId(message) {
  const rawSender = String(message.from || "").trim();
  if (!rawSender) {
    return "";
  }

  if (!/@lid$/i.test(rawSender)) {
    return normalizeSender(rawSender);
  }

  try {
    if (typeof client.getContactLidAndPhone === "function") {
      const matches = await client.getContactLidAndPhone([rawSender]);
      const phoneNumber = matches && matches[0] && matches[0].pn;
      if (phoneNumber) {
        return phoneNumber.trim();
      }
    }
  } catch (error) {
    console.warn(`LID -> telefon eslesemedi (${rawSender}):`, error.message || error);
  }

  try {
    const contact = await message.getContact();
    if (contact && contact.number) {
      return String(contact.number).trim();
    }
    if (contact && contact.id && contact.id.user) {
      return String(contact.id.user).trim();
    }
  } catch (error) {
    console.warn(`Kontaktan telefon alinamadi (${rawSender}):`, error.message || error);
  }

  return rawSender.replace(/@lid$/i, "").trim();
}

async function postToAgent(payload) {
  const headers = { "Content-Type": "application/json" };
  if (BRIDGE_TOKEN) {
    headers["X-Bridge-Token"] = BRIDGE_TOKEN;
  }

  const response = await fetch(BRIDGE_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Bridge error ${response.status}: ${text}`);
  }

  return response.json();
}

async function sendFollowUpActions(chat, actions) {
  for (const action of actions || []) {
    if (action.type === "text" && action.message) {
      await chat.sendMessage(action.message);
      continue;
    }

    if (action.type === "location" && action.latitude && action.longitude) {
      const label = action.label ? `\n${action.label}` : "";
      const mapsUrl = `https://www.google.com/maps?q=${action.latitude},${action.longitude}`;
      await chat.sendMessage(`Konum${label}\n${mapsUrl}`);
    }
  }
}

client.on("qr", (qr) => {
  console.log("\nWhatsApp QR hazir. Telefonda WhatsApp > Linked Devices > Link a Device ile tara:\n");
  qrcode.generate(qr, { small: true });
});

function bridgeHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (BRIDGE_TOKEN) {
    headers["X-Bridge-Token"] = BRIDGE_TOKEN;
  }
  return headers;
}

function toChatId(phoneNumber) {
  const digits = String(phoneNumber || "").replace(/\D/g, "");
  return digits ? `${digits}@c.us` : null;
}

// Panelden "canli devralma" ile yazilan mesajlari kuyruktan cekip gonderir.
async function drainOutbox() {
  try {
    const response = await fetch(`${BRIDGE_BASE}/outbox`, { headers: bridgeHeaders() });
    if (!response.ok) return;
    const payload = await response.json();

    for (const item of payload.messages || []) {
      let ok = false;
      try {
        const chatId = toChatId(item.user_id);
        if (chatId) {
          await client.sendMessage(chatId, item.message);
          ok = true;
          console.log(`Panel mesaji gonderildi -> ${item.user_id}`);
        }
      } catch (error) {
        console.error(`Panel mesaji gonderilemedi (${item.user_id}):`, error.message || error);
      }
      await fetch(`${BRIDGE_BASE}/outbox/${item.id}/ack`, {
        method: "POST",
        headers: bridgeHeaders(),
        body: JSON.stringify({ ok }),
      }).catch(() => {});
    }
  } catch (error) {
    // Kopru gecici olarak kapali olabilir; sessizce bir sonraki turu bekle.
  }
}

client.on("ready", () => {
  console.log("WhatsApp bot hazir. Gelen mesajlar VoiceAgent bridge'ine aktarilacak.");
  console.log(`Bridge URL: ${BRIDGE_URL}`);
  console.log(`Auth klasoru: ${AUTH_PATH}`);
  setInterval(drainOutbox, OUTBOX_POLL_MS);
});

client.on("authenticated", () => {
  console.log("WhatsApp oturumu dogrulandi.");
});

client.on("auth_failure", (message) => {
  console.error("WhatsApp auth failure:", message);
});

client.on("disconnected", (reason) => {
  console.warn("WhatsApp baglantisi koptu:", reason);
});

// Yalnizca birebir (1-1) sohbetlere yanit ver. Durum guncellemeleri
// (status@broadcast), yayin listeleri, gruplar ve kanallar atlanir — bunlara
// cevap yazmak hem yanlis hem de whatsapp-web.js'te status gonderim yolundaki
// bir hataya (canCheckStatusRankingPosterGating) yol acar.
function isIgnorableChat(message) {
  const from = String(message.from || "");
  if (message.isStatus === true) return true;
  return (
    from === "status@broadcast" ||
    from.endsWith("@broadcast") ||
    from.endsWith("@g.us") ||
    from.endsWith("@newsletter")
  );
}

client.on("message", async (message) => {
  if (message.fromMe) {
    return;
  }

  if (!message.body || !message.body.trim()) {
    return;
  }

  if (isIgnorableChat(message)) {
    return;
  }

  try {
    console.log(`Mesaj alindi: ${message.from} -> ${message.body}`);
    const senderId = await resolveSenderId(message);
    const payload = await postToAgent({
      phone_number: senderId,
      message: message.body
    });

    if (payload.reply) {
      await message.reply(payload.reply);
    }

    // Follow-up (konum paylasimi vb.) YALNIZCA varsa yapilir ve KENDI
    // try/catch'inde durur. Onceden burada kosulsuz message.getChat()
    // cagriliyordu; bu, bazi kimliklerde (@lid) patliyor, asil cevap ZATEN
    // gittigi halde asagidaki catch tetiklenip her mesajin pesine "Sistem
    // tarafinda gecici bir sorun oldu" spam'i gonderiyordu.
    if (Array.isArray(payload.follow_up_actions) && payload.follow_up_actions.length) {
      try {
        const chat = await message.getChat();
        await sendFollowUpActions(chat, payload.follow_up_actions);
      } catch (followError) {
        console.error("Follow-up gonderilemedi:", followError.message || followError);
      }
    }
  } catch (error) {
    console.error("Mesaj islenemedi:", error);
    // Hata mesaji gonderimi de basarisiz olabilir; sureci dusurmesin.
    try {
      await message.reply("Sistem tarafinda gecici bir sorun oldu. Birazdan tekrar dener misiniz?");
    } catch (replyError) {
      console.error("Hata yaniti gonderilemedi:", replyError.message || replyError);
    }
  }
});

// Tek bir mesaj/gonderim hatasi tum botu (ve supervisor uzerinden tum
// servisleri) dusurmesin; logla ve calismaya devam et.
process.on("unhandledRejection", (reason) => {
  console.error("Yakalanmamis promise reddi:", (reason && reason.message) || reason);
});

client.initialize();
