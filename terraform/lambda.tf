# Empaquetage : Terraform fabrique le zip a partir du dossier src/.
#
# Aucune dependance externe a installer — le code n'utilise que la
# bibliotheque standard, et boto3 est fourni par le runtime Lambda. C'est
# ce qui permet cet empaquetage trivial, sans etape de build.

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/build/lambda.zip"

  excludes = ["__pycache__", "cli.py"]
}


# --- Groupes de logs ------------------------------------------------------
# Declares explicitement plutot que laisses a la creation automatique par
# Lambda. Deux raisons : fixer une retention (sinon les logs s'accumulent
# indefiniment et finissent par couter), et pouvoir les nommer dans les
# politiques IAM.

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/aws/lambda/${var.project_name}-collector"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "scheduler" {
  name              = "/aws/lambda/${var.project_name}-scheduler"
  retention_in_days = var.log_retention_days
}


# --- Lambda de collecte ---------------------------------------------------

resource "aws_lambda_function" "collector" {
  function_name = "${var.project_name}-collector"
  role          = aws_iam_role.collector.arn
  handler       = "handler_collector.handler"
  runtime       = "python3.13"
  architectures = ["arm64"] # ~20 % moins cher que x86 a performance egale

  filename         = data.archive_file.lambda.output_path
  # Force le redeploiement quand le code change. Sans ce hash, Terraform ne
  # verrait aucune difference et ne redeploierait jamais.
  source_code_hash = data.archive_file.lambda.output_base64sha256

  timeout     = var.lambda_timeout
  memory_size = 512 # la charge est reseau, pas calcul

  environment {
    variables = {
      RAW_BUCKET        = aws_s3_bucket.raw.id
      API_KEY_PARAMETER = var.api_key_parameter
    }
  }

  # CONCURRENCE RESERVEE DESACTIVEE.
  #
  # Un compte AWS neuf est plafonne a 10 executions simultanees au lieu de
  # 1000, et AWS exige d'en laisser au moins 10 non reservees. Reserver quoi
  # que ce soit est donc impossible avant une hausse de quota.
  #
  # Le bridage du debit reste assure cote file, par maximum_concurrency sur
  # le branchement SQS (voir sqs.tf). On perd la double securite, pas la
  # securite. A retablir apres une demande d'augmentation de quota :
  #   reserved_concurrent_executions = var.collector_max_concurrency

  depends_on = [
    aws_iam_role_policy.collector,
    aws_cloudwatch_log_group.collector,
  ]
}


# --- Lambda de planification ---------------------------------------------

resource "aws_lambda_function" "scheduler" {
  function_name = "${var.project_name}-scheduler"
  role          = aws_iam_role.scheduler.arn
  handler       = "handler_scheduler.handler"
  runtime       = "python3.13"
  architectures = ["arm64"]

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      QUEUE_URL       = aws_sqs_queue.collect.url
      LOOKBACK_MONTHS = var.lookback_months
    }
  }

  depends_on = [
    aws_iam_role_policy.scheduler,
    aws_cloudwatch_log_group.scheduler,
  ]
}
