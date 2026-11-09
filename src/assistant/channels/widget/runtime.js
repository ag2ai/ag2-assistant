/* A card button asks the bot with its data; a link opens outside the chat. */
var widget = typeof TelegramWidget == "undefined" ? null : TelegramWidget;
function ask(el) {
  if (!widget || el.disabled) return;
  el.disabled = true;
  el.className = "";
  widget.request(el.getAttribute("data-query")).then(
    function () { el.disabled = false; },
    function () { el.disabled = false; el.className = "failed"; }
  );
}
function openLink(el) {
  if (widget) widget.openLink(el.getAttribute("data-url"));
}
if (widget) widget.ready();
