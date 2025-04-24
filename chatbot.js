// === CONFIGURE YOUR DETAILS ===
const REGION = 'us-east-1'; // your bot region
const IDENTITY_POOL_ID = 'us-east-1:####'; // Cognito Identity Pool
const BOT_ID = '####'; // Lex Bot ID
const BOT_ALIAS_ID = '####'; // Lex Bot Alias ID
const LOCALE_ID = 'en_US';

AWS.config.region = REGION;
AWS.config.credentials = new AWS.CognitoIdentityCredentials({
    IdentityPoolId: IDENTITY_POOL_ID
});

const lexruntimev2 = new AWS.LexRuntimeV2();

let sessionId = Date.now().toString();

function appendMessage(sender, text) {
  const chatBox = document.getElementById("chat-box");
  const msg = document.createElement("div");
  msg.innerHTML = `<strong>${sender}:</strong> ${text}`;
  chatBox.appendChild(msg);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function sendMessage() {
  const input = document.getElementById("user-input");
  const message = input.value;
  if (!message.trim()) return;

  appendMessage("You", message);
  input.value = "";

  const params = {
    botId: BOT_ID,
    botAliasId: BOT_ALIAS_ID,
    localeId: LOCALE_ID,
    sessionId: sessionId,
    text: message
  };

  lexruntimev2.recognizeText(params, function (err, data) {
    if (err) {
      console.error(err);
      appendMessage("Bot", "Error: " + err.message);
    } else {
      const messages = data.messages || [];
      messages.forEach(m => {
        appendMessage("Bot", m.content);
      });
    }
  });
}
