let valA = '';
let valB = '';
let currentOp = '';
const display = document.getElementById('display');

function updateDisplay(content) { 
    if (display) display.innerText = content || '0'; 
}

function addNum(num) {
    if (!currentOp) {
        valA += num;
        updateDisplay(valA);
    } else {
        valB += num;
        updateDisplay(valA + ' ' + getOpSymbol(currentOp) + ' ' + valB);
    }
}

function setOp(op) {
    if (!valA) return;
    currentOp = op;
    updateDisplay(valA + ' ' + getOpSymbol(currentOp) + ' ');
}

function getOpSymbol(op) {
    const symbols = {add:'+', sub:'-', mul:'×', div:'÷', pow:'^', mod:'%'};
    return symbols[op] || op;
}

function calcSqrt() {
    if (!valA) return;
    window.location.href = `/calc?a=${valA}&b=0&op=sqrt`;
}

function calculate() {
    if (!valA || !valB || !currentOp) return;
    window.location.href = `/calc?a=${valA}&b=${valB}&op=${currentOp}`;
}

function clearDisplay() {
    valA = ''; valB = ''; currentOp = '';
    updateDisplay('0');
}