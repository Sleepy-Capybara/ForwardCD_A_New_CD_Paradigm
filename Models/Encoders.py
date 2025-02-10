import torchimport torch.nn as nnimport mathimport torch.nn.functional as Fimport torch.nn.utils.rnn as rnn_utilsclass RaschEmbedding(nn.Module):    def __init__(self, n_exercise, n_concept, Q_matrix, embedding_dim):        super().__init__()        self.exer_emb = nn.Embedding(n_exercise, embedding_dim)        self.exer_lam = nn.Embedding(n_exercise, 1)        self.concept_emb = nn.Embedding(n_concept, embedding_dim)        self.Q_matrix = Q_matrix    def forward(self, exercise):        exer_embedding = self.exer_emb(exercise)  # (n_exer, embedding_dim)        exer_lambda = self.exer_lam(exercise)  # (n_exer, 1)        related_concepts = self.Q_matrix[exercise]  # (n_exer, n_concept)        concept_embedding_sum = torch.matmul(related_concepts, self.concept_emb.weight)  # (n_exer, embedding_dim)        concept_count = torch.sum(related_concepts, dim=1, keepdim=True)  # (n_exer, 1)        concept_embedding = concept_embedding_sum / concept_count  # (n_exer, embedding_dim)        rasch_embedding = exer_embedding + exer_lambda * concept_embedding        return rasch_embeddingclass NaiveEncoder(nn.Module):    def __init__(self, q_matrix):        super().__init__()        self.Q_matrix = q_matrix    def forward(self, p_matrix):        correct_num_matrix = (p_matrix == 2).to(torch.float) @ self.Q_matrix        total_num_matrix = (p_matrix != 0).to(torch.float) @ self.Q_matrix        correct_rate_matrix = correct_num_matrix / total_num_matrix        correct_rate_matrix = torch.nan_to_num(correct_rate_matrix, nan=0.5)        return correct_rate_matrixclass MlpEncoder(nn.Module):    def __init__(self, n_exercise, n_concept):        super().__init__()        self.p_embedding = nn.Embedding(3, 32, padding_idx=0)        self.mlp = nn.Sequential(            nn.Linear(32 * n_exercise, 512),            nn.ReLU(),            nn.Linear(512, 256),            nn.ReLU(),            nn.Linear(256, n_concept),            nn.Sigmoid()        )    def forward(self, p_matrix):        batch_size = p_matrix.shape[0]        p_emb = self.p_embedding(p_matrix.to(torch.int)).reshape(batch_size, -1)        theta = self.mlp(p_emb)        return thetaclass EmbEncoder(nn.Module):    def __init__(self, n_stu, out_dim):        super().__init__()        self.emb = nn.Embedding(n_stu, out_dim)    def forward(self, sid):        return torch.sigmoid(self.emb(sid))class AttentionEncoder(nn.Module):    def __init__(self, n_exercise, out_dim, n_concept=None, embedding_dim=128, out_sigmoid=True, block_num=1, pooling="mean", q_matrix=None):        super().__init__()        if n_concept is None:            n_concept = out_dim//2        self.embedding_dim = embedding_dim        # self.exercise_embedding = nn.Embedding(n_exercise, embedding_dim)        self.exercise_embedding = RaschEmbedding(n_exercise, n_concept, q_matrix, embedding_dim)        self.response_embedding = nn.Embedding(3, embedding_dim)  # correct（2），incorrect（1），unattempted（0）        self.attention_block = AttentionBlock(embedding_dim, embedding_dim)        self.attention_blocks = self.model_blocks = nn.ModuleList([AttentionBlock(embedding_dim, embedding_dim) for _ in range(block_num)])        self.er_linear = nn.Linear(2*embedding_dim, embedding_dim)        if out_sigmoid:            self.map_layer = nn.Sequential(                nn.Linear(embedding_dim, out_dim),                nn.Sigmoid()            )        else:            self.map_layer = nn.Linear(embedding_dim, out_dim)        self.pooling =pooling    def forward(self, p_matrix):        non_zero_indices = torch.nonzero(p_matrix).cuda()        non_zero_count_per_sample = non_zero_indices[:, 0].bincount(minlength=p_matrix.shape[0])        max_non_zero_count = non_zero_count_per_sample.max().item()        padded_exercise_embed = torch.zeros((p_matrix.shape[0], max_non_zero_count, self.embedding_dim)).cuda()        padded_response_embed = torch.zeros((p_matrix.shape[0], max_non_zero_count, self.embedding_dim)).cuda()        padded_er_embed = torch.zeros((p_matrix.shape[0], max_non_zero_count, self.embedding_dim)).cuda()        exercise_idx = non_zero_indices[:, 1]        response_idx = p_matrix[non_zero_indices[:, 0], exercise_idx]        exercise_embed = self.exercise_embedding(exercise_idx)  # (num_nonzero, embedding_dim)        response_embed = self.response_embedding(response_idx.to(torch.int))  # (num_nonzero, embedding_dim)        er_embed = self.er_linear(torch.concatenate([exercise_embed, response_embed], dim=1))        student_idx = non_zero_indices[:, 0]        position_idx = torch.cat([torch.arange(count, device=p_matrix.device) for count in non_zero_count_per_sample])        padded_exercise_embed[student_idx, position_idx] = exercise_embed        padded_response_embed[student_idx, position_idx] = response_embed        padded_er_embed[student_idx, position_idx] = er_embed        """        The following code has the same functionality as the previous one.         Although it runs slightly slower due to the presence of the for loop, it is easier to understand.                exercise_embeds = []        response_embeds = []        er_embeds = []        lengths = []                for i in range(batch_size):            non_zero_indices = torch.nonzero(p_matrix[i]).squeeze(1)            exercise_indices = non_zero_indices            responses = p_matrix[i, exercise_indices]            exercise_embed = self.exercise_embedding(exercise_indices)  # (num_nonzero, embedding_dim)            response_embed = self.response_embedding(responses.to(torch.int))  # (num_nonzero, embedding_dim)            er_embed = self.er_linear(torch.concatenate([exercise_embed, response_embed], dim=1))            exercise_embeds.append(exercise_embed)            response_embeds.append(response_embed)            er_embeds.append(er_embed)            lengths.append(exercise_embed.size(0))        padded_exercise_embed = rnn_utils.pad_sequence(exercise_embeds, batch_first=True)        padded_response_embed = rnn_utils.pad_sequence(response_embeds, batch_first=True)        padded_er_embed = rnn_utils.pad_sequence(er_embeds, batch_first=True)        """        mask = (torch.arange(max_non_zero_count).expand(p_matrix.shape[0], max_non_zero_count).cuda() < non_zero_count_per_sample.unsqueeze(1))        theta = self.attention_block(padded_response_embed, padded_exercise_embed, padded_response_embed, mask)        if len(self.attention_blocks) > 1:            for attention_block in self.attention_blocks[1:]:                theta = attention_block(theta, theta, theta, mask)        if self.pooling == "mean":            mask_expanded = mask.unsqueeze(-1).float()  # (batch_size, max_len, 1)            masked_theta = theta * mask_expanded  # (batch_size, max_len, dim)            valid_counts = mask_expanded.sum(dim=1)  # (batch_size, 1)            valid_counts = valid_counts.clamp(min=1)            averaged_theta = masked_theta.sum(dim=1) / valid_counts  # (batch_size, dim)        elif self.pooling == "max":            mask_expanded = mask.unsqueeze(-1).float()  # (batch_size, max_len, 1)            min_value = torch.min(theta)            theta = torch.where(mask_expanded == 0, min_value, theta)            averaged_theta, _ = torch.max(theta, dim=1)        theta = self.map_layer(averaged_theta)  # 对题目结果聚合        return thetaclass AttentionBlock(nn.Module):    def __init__(self, in_features, out_features, num_heads=4):        super().__init__()        assert out_features % num_heads == 0        self.out_features = out_features // num_heads        self.num_heads = num_heads        self.W_v = nn.Linear(in_features, out_features)        self.W_q = nn.Linear(in_features, out_features)        self.W_k = nn.Linear(in_features, out_features)    def forward(self, q, k, v, mask=None):        mapped_v = self.W_v(v).reshape(v.size(0), -1, self.num_heads, self.out_features).permute(2, 0, 1, 3)        mapped_q = self.W_q(q).reshape(q.size(0), -1, self.num_heads, self.out_features).permute(2, 0, 1, 3)        mapped_k = self.W_k(k).reshape(k.size(0), -1, self.num_heads, self.out_features).permute(2, 0, 1, 3)        attn_score = torch.matmul(mapped_q, mapped_k.transpose(-1, -2)) / (self.out_features ** 0.5)        if mask is not None:            expanded_mask = mask.unsqueeze(1) & mask.unsqueeze(2)            expanded_mask = expanded_mask.unsqueeze(0)            attn_score = attn_score.masked_fill(~expanded_mask, float('-inf'))        attention = F.softmax(attn_score, dim=-1)        attention = attention.masked_fill(~expanded_mask, 0)        out = torch.matmul(attention, mapped_v).permute(1, 2, 0, 3).reshape(v.size(0), -1,                                                                            self.num_heads * self.out_features)        return out