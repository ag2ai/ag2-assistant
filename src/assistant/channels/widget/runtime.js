/* A card button asks the bot with its data and shows what the bot answers; a link opens
   outside the chat. */
var widget = typeof TelegramWidget == "undefined" ? null : TelegramWidget;
function ask(el) {
  if (!widget || el.disabled) return;
  el.disabled = true;
  el.className = "";
  widget.request(el.getAttribute("data-query")).then(
    function (answer) { el.disabled = false; tell(answer && answer.result); },
    function () { el.disabled = false; el.className = "failed"; }
  );
}
function tell(text) {
  var note = document.getElementById("answer");
  if (!note) {
    note = document.createElement("div");
    note.id = "answer";
    note.className = "text caption muted";
    document.getElementById("page").appendChild(note);
  }
  note.textContent = text || "";
}
function openLink(el) {
  if (widget) widget.openLink(el.getAttribute("data-url"));
}
if (widget) widget.ready();
