let tempo = 0; // Tempo em segundos
let cronometro;
let cronometroElement = document.getElementById('cronometro');

// Função para formatar o tempo no formato HH:MM:SS
function formatarTempo(segundos) {
    let horas = Math.floor(segundos / 3600);
    let minutos = Math.floor((segundos % 3600) / 60);
    let segundosFormatados = Math.floor(segundos % 60); // Remove as casas decimais

    // Garante que horas, minutos e segundos tenham dois dígitos
    return `${String(horas).padStart(2, '0')}:${String(minutos).padStart(2, '0')}:${String(segundosFormatados).padStart(2, '0')}`;
}

// Função para atualizar o cronômetro
function atualizarCronometro() {
    tempo--;
    if (tempo < 0) {
        tempo = 0; // Garante que o tempo não seja negativo
        clearInterval(cronometro); // Para o cronômetro quando chegar a zero
        cronometro = null;
    }
    cronometroElement.textContent = formatarTempo(tempo);
}

// Função para buscar o tempo restante do servidor
function buscarTempoRestante() {
    fetch('/tempo_restante')
        .then(response => response.json())
        .then(data => {
            tempo = data.tempo_restante;
            cronometroElement.textContent = formatarTempo(tempo);
            if (tempo > 0) {
                cronometro = setInterval(atualizarCronometro, 1000);
            }
        });
}

// Configuração do cronômetro (apenas para avaliadores)
if (document.getElementById('iniciar-cronometro')) {
    document.getElementById('iniciar-cronometro').addEventListener('click', () => {
        const horas = parseInt(document.getElementById('horas').value) || 0;
        const minutos = parseInt(document.getElementById('minutos').value) || 0;

        fetch('/iniciar_cronometro', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ horas, minutos }),
        })
        .then(response => response.json())
        .then(data => {
            if (data.erro) {
                alert(data.erro);
            } else {
                buscarTempoRestante(); // Atualiza o cronômetro após iniciar
            }
        });
    });
}

// Busca o tempo restante ao carregar a página
buscarTempoRestante();