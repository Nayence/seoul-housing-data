# File de travail : chaque message = un arrondissement pour un mois.
#
# POURQUOI UNE FILE plutot qu'une Lambda qui boucle sur tout :
#   - une reprise historique represente ~12 000 appels API, impossible dans
#     les 15 minutes maximum d'une invocation
#   - la concurrence reservee de la Lambda consommatrice bride le debit et
#     protege le quota de 10 000 appels/jour de data.go.kr
#   - les reessais et la file de rebut sont natifs, sans code a ecrire

resource "aws_sqs_queue" "collect_dlq" {
  name = "${var.project_name}-collect-dlq"

  # 14 jours : le temps de remarquer le probleme et de l'analyser.
  # Les messages ici ont echoue plusieurs fois, ils meritent un examen.
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "collect" {
  name = "${var.project_name}-collect"

  # REGLE AWS : le delai d'invisibilite doit valoir au moins 6x le timeout
  # de la Lambda consommatrice. Sinon un message peut redevenir visible et
  # etre traite une seconde fois alors que la premiere tourne encore.
  visibility_timeout_seconds = var.lambda_timeout * 6

  message_retention_seconds = 345600 # 4 jours

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.collect_dlq.arn
    # Apres 3 echecs, le message part en rebut au lieu de boucler
    # indefiniment en consommant du quota API.
    maxReceiveCount = 3
  })
}

# Branchement de la file sur la Lambda de collecte.
resource "aws_lambda_event_source_mapping" "collect" {
  event_source_arn = aws_sqs_queue.collect.arn
  function_name    = aws_lambda_function.collector.arn

  # Petits lots : un message = ~10 secondes de travail. Un lot de 2 tient
  # largement dans le timeout, et un echec n'affecte que peu de messages.
  batch_size                         = 2
  maximum_batching_window_in_seconds = 0

  # Sans cette option, un seul message en echec ferait rejouer TOUT le lot,
  # y compris les messages deja traites avec succes. Le handler renvoie
  # batchItemFailures ; c'est cette ligne qui le rend effectif.
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    # LE BRIDAGE DU DEBIT, c'est ici. 2 invocations simultanees maximum,
    # soit environ 8 appels API en parallele. Loin des limites du portail.
    maximum_concurrency = var.collector_max_concurrency
  }
}
