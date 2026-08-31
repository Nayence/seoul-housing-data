# IAM au plus juste.
#
# C'est la partie qu'un recruteur cloud regarde en premier. Chaque politique
# nomme des ressources precises, jamais "*". Une Lambda qui n'a besoin que
# d'ecrire dans un prefixe S3 n'obtient que ce droit, sur ce prefixe.

# Document d'approbation commun aux deux Lambdas : seul le service Lambda
# peut endosser ces roles.
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}


# --- Lambda de collecte ---------------------------------------------------

resource "aws_iam_role" "collector" {
  name               = "${var.project_name}-collector"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "collector" {
  # Journalisation. Restreinte au groupe de logs de cette fonction, pas a
  # l'ensemble des logs du compte.
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.collector.arn}:*"]
  }

  # Ecriture des donnees. Uniquement sous le prefixe normalized/, et
  # uniquement en ecriture : la Lambda n'a aucun droit de lecture ni de
  # suppression sur le bucket.
  statement {
    sid       = "WriteNormalizedData"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/normalized/*"]
  }

  # Lecture de la cle API. Un seul parametre nomme, pas le chemin entier.
  statement {
    sid     = "ReadApiKey"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${var.api_key_parameter}"
    ]
  }

  # Dechiffrement du SecureString. Sans cette permission, l'appel SSM
  # echoue avec un refus d'acces KMS peu explicite — piege classique.
  statement {
    sid       = "DecryptParameter"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }

  # Consommation de la file. GetQueueAttributes est requis par le mecanisme
  # de branchement, il ne suffit pas de recevoir et supprimer.
  statement {
    sid = "ConsumeQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.collect.arn]
  }
}

resource "aws_iam_role_policy" "collector" {
  name   = "${var.project_name}-collector"
  role   = aws_iam_role.collector.id
  policy = data.aws_iam_policy_document.collector.json
}


# --- Lambda de planification ---------------------------------------------

resource "aws_iam_role" "scheduler" {
  name               = "${var.project_name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.scheduler.arn}:*"]
  }

  # Depot dans la file uniquement. Le planificateur ne peut ni lire ni
  # supprimer les messages : il ne fait qu'alimenter.
  statement {
    sid       = "EnqueueWork"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.collect.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${var.project_name}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}


# --- Role de l'ordonnanceur EventBridge -----------------------------------
# EventBridge Scheduler n'invoque pas directement : il endosse un role qui
# porte la permission d'invoquer la fonction.

data "aws_iam_policy_document" "eventbridge_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    # Empeche le probleme du "deputy confus" : seul un ordonnanceur de TON
    # compte peut endosser ce role, pas celui d'un tiers.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "eventbridge" {
  name               = "${var.project_name}-eventbridge"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_assume.json
}

data "aws_iam_policy_document" "eventbridge" {
  statement {
    sid       = "InvokeScheduler"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.scheduler.arn]
  }
}

resource "aws_iam_role_policy" "eventbridge" {
  name   = "${var.project_name}-eventbridge"
  role   = aws_iam_role.eventbridge.id
  policy = data.aws_iam_policy_document.eventbridge.json
}
